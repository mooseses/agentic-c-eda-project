# benchmarks/system1_bench.py
"""
System 1 Benchmark: Throughput, CPU, Memory, and Volume Reduction
Measures the reflex layer performance under controlled load.

Outputs:
- system1_metrics.csv: Per-second metrics (events, CPU, memory, reduction)
- system1_pipeline.json: Aggregate counts by pipeline stage
"""
import os
import sys
import time
import json
import csv
import psutil
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from watchdog import LogWatchdog, NOISE_PATTERNS
    from config import LOG_FILES, IGNORED_PORTS, INTERNAL_SUBNET, TRUSTED_INTERNAL_PORTS
    import config
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    LOG_FILES = []
    NOISE_PATTERNS = ()

# Import parsing regexes directly for replay mode
import re
RE_SRC = re.compile(r'SRC=([\d.]+)')
RE_DPT = re.compile(r'DPT=(\d+)')
RE_PROTO = re.compile(r'PROTO=(\w+)')
RE_IP_FROM = re.compile(r'from ([\d.]+)')
RE_USER = re.compile(r'for (\w+)')
RE_SYSLOG_TIME = re.compile(r'^(\w{3})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})')

NETWORK_TAG = "[Sentinel]"
INTERNAL_SUBNET_DEFAULT = "10.0.0."

# Noise patterns for filtering
NOISE_PATTERNS_DEFAULT = (
    "apparmor=",
    "audit:",
    "IN=lo",
    "DST=224.0.0.251",
    "DST=255.255.255.255",
    "systemd-logind",
    "CRON",
)

# Ports to filter as noise
IGNORED_PORTS_DEFAULT = {"5353", "5355", "1900", "137", "138", "67", "68"}

# Trusted internal ports (for trust filter demo)
TRUSTED_INTERNAL_PORTS_DEFAULT = {22, 80, 443, 8080}


@dataclass
class PipelineMetrics:
    """Metrics for each pipeline stage."""
    timestamp: str
    raw_lines: int = 0
    noise_filtered: int = 0
    trust_filtered: int = 0
    parse_failed: int = 0
    events_output: int = 0
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    parse_latency_us: float = 0.0


class InstrumentedWatchdog(LogWatchdog):
    """Watchdog with instrumentation for benchmarking."""
    
    def __init__(self):
        super().__init__()
        self.metrics = PipelineMetrics(timestamp=datetime.now().isoformat())
        self._process = psutil.Process()
        
    def reset_metrics(self):
        """Reset counters for new measurement window."""
        self.metrics = PipelineMetrics(timestamp=datetime.now().isoformat())
        
    def get_metrics(self) -> PipelineMetrics:
        """Get current metrics with resource usage."""
        self.metrics.cpu_percent = self._process.cpu_percent()
        self.metrics.memory_mb = self._process.memory_info().rss / (1024 * 1024)
        self.metrics.timestamp = datetime.now().isoformat()
        return self.metrics
    
    def _read_new_lines_instrumented(self):
        """Instrumented version that counts at each pipeline stage."""
        import re
        from watchdog import (RE_SRC, RE_DPT, RE_SYSLOG_TIME, NETWORK_TAG, 
                              RE_IP_FROM, RE_USER, RE_PROTO)
        
        for filepath, fh in list(self.file_handles.items()):
            self._check_rotation(filepath)
            fh = self.file_handles.get(filepath)
            if not fh:
                continue

            try:
                fh.seek(self.file_positions[filepath])
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    
                    self.metrics.raw_lines += 1
                    
                    # Timestamp filter
                    log_time = self._parse_log_time(line)
                    if log_time and self.start_time and log_time < self.start_time:
                        continue

                    # Noise gate
                    if self._is_noise(line):
                        self.metrics.noise_filtered += 1
                        continue
                    
                    # Trust filter
                    if self._is_trusted_internal(line):
                        self.metrics.trust_filtered += 1
                        continue

                    # Parse
                    start_us = time.perf_counter_ns() / 1000
                    event = self._parse(line)
                    parse_time = (time.perf_counter_ns() / 1000) - start_us
                    
                    if event:
                        self.pending_lines.append(event)
                        self.metrics.events_output += 1
                        self.metrics.parse_latency_us = (
                            (self.metrics.parse_latency_us * (self.metrics.events_output - 1) + parse_time)
                            / self.metrics.events_output
                        )
                    else:
                        self.metrics.parse_failed += 1

                self.file_positions[filepath] = fh.tell()
            except (IOError, OSError):
                pass
    
    def read_stream_instrumented(self) -> Optional[str]:
        """Instrumented read that tracks metrics."""
        if self.pending_lines:
            return self.pending_lines.pop(0)
        
        self._read_new_lines_instrumented()
        
        if self.pending_lines:
            return self.pending_lines.pop(0)
        
        time.sleep(0.1)
        return None


class ReplayWatchdog:
    """
    Replay mode watchdog for Windows compatibility.
    Processes a log file without requiring live Linux system logs.
    """
    
    def __init__(self, log_file: str):
        self.log_file = log_file
        self.metrics = PipelineMetrics(timestamp=datetime.now().isoformat())
        self._process = psutil.Process()
        self.pending_lines = []
        self.all_lines = []
        self.current_idx = 0
        
    def start_stream(self):
        """Load all lines from log file."""
        with open(self.log_file, 'r', encoding='utf-8') as f:
            self.all_lines = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        print(f"  [+] Loaded {len(self.all_lines)} lines from {self.log_file}")
        
    def stop_stream(self):
        """Cleanup."""
        self.all_lines = []
        self.current_idx = 0
        
    def reset_metrics(self):
        """Reset counters for new measurement window."""
        self.metrics = PipelineMetrics(timestamp=datetime.now().isoformat())
        
    def get_metrics(self) -> PipelineMetrics:
        """Get current metrics with resource usage."""
        self.metrics.cpu_percent = self._process.cpu_percent()
        self.metrics.memory_mb = self._process.memory_info().rss / (1024 * 1024)
        self.metrics.timestamp = datetime.now().isoformat()
        return self.metrics
    
    def _is_noise(self, line: str) -> bool:
        """Check if line matches noise patterns."""
        for pattern in NOISE_PATTERNS_DEFAULT:
            if pattern in line:
                return True
        dpt_match = RE_DPT.search(line)
        if dpt_match and dpt_match.group(1) in IGNORED_PORTS_DEFAULT:
            return True
        return False
    
    def _is_trusted_internal(self, line: str) -> bool:
        """Check if this is trusted internal traffic."""
        src_match = RE_SRC.search(line)
        dpt_match = RE_DPT.search(line)
        
        if src_match and dpt_match:
            src_ip = src_match.group(1)
            port = int(dpt_match.group(1))
            is_internal = src_ip.startswith(INTERNAL_SUBNET_DEFAULT)
            is_trusted = port in TRUSTED_INTERNAL_PORTS_DEFAULT
            if is_internal and is_trusted:
                return True
        return False
    
    def _parse(self, line: str) -> Optional[str]:
        """Parse a log line into structured event."""
        # Network events
        if NETWORK_TAG in line:
            src = RE_SRC.search(line)
            if not src:
                return None
            if "PROTO=ICMP" in line:
                return f"NET_PING Source={src.group(1)}"
            dpt = RE_DPT.search(line)
            proto = RE_PROTO.search(line)
            if dpt:
                return f"NET_CONN Source={src.group(1)} Port={dpt.group(1)} Proto={proto.group(1) if proto else '?'}"
        
        # SSH events
        if "sshd" in line and "Failed password" in line:
            ip = RE_IP_FROM.search(line)
            user = RE_USER.search(line)
            return f"SSH_AUTH_FAIL User={user.group(1) if user else 'unknown'} Source={ip.group(1) if ip else 'unknown'}"
        
        if "sshd" in line and "Accepted" in line:
            ip = RE_IP_FROM.search(line)
            user = RE_USER.search(line)
            method = "key" if "publickey" in line else "password"
            return f"SSH_AUTH_SUCCESS User={user.group(1) if user else 'unknown'} Source={ip.group(1) if ip else 'unknown'} Method={method}"
        
        if "sshd" in line and "Invalid user" in line:
            ip = RE_IP_FROM.search(line)
            user_match = re.search(r'Invalid user (\w+)', line)
            return f"SSH_INVALID_USER User={user_match.group(1) if user_match else 'unknown'} Source={ip.group(1) if ip else 'unknown'}"
        
        # Sudo events
        if "sudo:" in line and "COMMAND=" in line:
            user_match = re.search(r'sudo: (\w+) :', line)
            cmd_match = re.search(r'COMMAND=(.+)$', line)
            return f"SUDO_EXEC User={user_match.group(1) if user_match else 'unknown'} Command={cmd_match.group(1) if cmd_match else 'unknown'}"
        
        if "sudo" in line and "authentication failure" in line:
            user_match = re.search(r'logname=(\w+)', line)
            return f"SUDO_AUTH_FAIL User={user_match.group(1) if user_match else 'unknown'}"
        
        # Session events
        if "session opened" in line and "pam_unix" in line:
            user_match = re.search(r'for user (\w+)', line)
            service_match = re.search(r'pam_unix\((\w+)', line)
            if service_match and service_match.group(1) not in ('sudo', 'cron'):
                return f"SESSION_OPEN Service={service_match.group(1)} User={user_match.group(1) if user_match else 'unknown'}"
        
        if "session closed" in line and "pam_unix" in line:
            user_match = re.search(r'for user (\w+)', line)
            service_match = re.search(r'pam_unix\((\w+)', line)
            if service_match and service_match.group(1) not in ('sudo', 'cron'):
                return f"SESSION_CLOSE Service={service_match.group(1)} User={user_match.group(1) if user_match else 'unknown'}"
        
        return None
    
    def read_stream_instrumented(self) -> Optional[str]:
        """Process next line from replay file."""
        if self.pending_lines:
            return self.pending_lines.pop(0)
        
        # Process lines in batches for more realistic timing
        batch_size = 5
        processed = 0
        
        while self.current_idx < len(self.all_lines) and processed < batch_size:
            line = self.all_lines[self.current_idx]
            self.current_idx += 1
            self.metrics.raw_lines += 1
            processed += 1
            
            # Noise gate
            if self._is_noise(line):
                self.metrics.noise_filtered += 1
                continue
            
            # Trust filter
            if self._is_trusted_internal(line):
                self.metrics.trust_filtered += 1
                continue
            
            # Parse
            start_us = time.perf_counter_ns() / 1000
            event = self._parse(line)
            parse_time = (time.perf_counter_ns() / 1000) - start_us
            
            if event:
                self.pending_lines.append(event)
                self.metrics.events_output += 1
                if self.metrics.events_output > 0:
                    self.metrics.parse_latency_us = (
                        (self.metrics.parse_latency_us * (self.metrics.events_output - 1) + parse_time)
                        / self.metrics.events_output
                    )
            else:
                self.metrics.parse_failed += 1
        
        if self.pending_lines:
            return self.pending_lines.pop(0)
        
        # Finished all lines
        if self.current_idx >= len(self.all_lines):
            return None
            
        time.sleep(0.05)  # Simulate some processing time
        return None
    
    def is_complete(self) -> bool:
        """Check if all lines have been processed."""
        return self.current_idx >= len(self.all_lines) and not self.pending_lines


def run_benchmark_replay(log_file: str, output_dir: str = "benchmark_results"):
    """
    Run System 1 benchmark in replay mode (for Windows).
    
    Args:
        log_file: Path to log file to replay
        output_dir: Directory for output files
    """
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    print(f"=" * 60)
    print(f"  System 1 Benchmark - REPLAY MODE (Windows Compatible)")
    print(f"=" * 60)
    print(f"  Log file: {log_file}")
    print(f"  Output: {output_path.absolute()}")
    print(f"=" * 60)
    
    watchdog = ReplayWatchdog(log_file)
    watchdog.start_stream()
    
    metrics_file = output_path / "system1_metrics.csv"
    csv_file = open(metrics_file, 'w', newline='')
    writer = csv.DictWriter(csv_file, fieldnames=[
        'timestamp', 'raw_lines', 'noise_filtered', 'trust_filtered',
        'parse_failed', 'events_output', 'cpu_percent', 'memory_mb', 'parse_latency_us'
    ])
    writer.writeheader()
    
    start_time = time.time()
    total_metrics = PipelineMetrics(timestamp=datetime.now().isoformat())
    
    print(f"\n[*] Processing started at {datetime.now().strftime('%H:%M:%S')}\n")
    
    events_processed = []
    
    try:
        while not watchdog.is_complete():
            event = watchdog.read_stream_instrumented()
            if event:
                events_processed.append(event)
                
    except KeyboardInterrupt:
        print("\n[!] Benchmark interrupted")
    finally:
        # Get final metrics
        metrics = watchdog.get_metrics()
        writer.writerow(asdict(metrics))
        total_metrics = metrics
        
        csv_file.close()
        watchdog.stop_stream()
    
    # Calculate reduction ratios
    total_input = total_metrics.raw_lines or 1
    pipeline_summary = {
        "mode": "replay",
        "log_file": log_file,
        "duration_seconds": time.time() - start_time,
        "totals": {
            "raw_lines": total_metrics.raw_lines,
            "noise_filtered": total_metrics.noise_filtered,
            "trust_filtered": total_metrics.trust_filtered,
            "parse_failed": total_metrics.parse_failed,
            "events_output": total_metrics.events_output
        },
        "reduction_ratios": {
            "noise_gate": total_metrics.noise_filtered / total_input,
            "trust_filter": total_metrics.trust_filtered / total_input,
            "parse_fail": total_metrics.parse_failed / total_input,
            "total_reduction": 1 - (total_metrics.events_output / total_input)
        },
        "throughput": {
            "events_per_second": total_metrics.events_output / max(0.001, time.time() - start_time)
        },
        "sample_events": events_processed[:5]  # First 5 events for verification
    }
    
    summary_file = output_path / "system1_pipeline.json"
    with open(summary_file, 'w') as f:
        json.dump(pipeline_summary, f, indent=2)
    
    print(f"{'=' * 60}")
    print(f"  RESULTS SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total raw lines:     {total_metrics.raw_lines:,}")
    print(f"  Noise filtered:      {total_metrics.noise_filtered:,} ({pipeline_summary['reduction_ratios']['noise_gate']:.1%})")
    print(f"  Trust filtered:      {total_metrics.trust_filtered:,} ({pipeline_summary['reduction_ratios']['trust_filter']:.1%})")
    print(f"  Parse failures:      {total_metrics.parse_failed:,}")
    print(f"  Events output:       {total_metrics.events_output:,}")
    print(f"  Total reduction:     {pipeline_summary['reduction_ratios']['total_reduction']:.1%}")
    print(f"\n  Sample parsed events:")
    for e in events_processed[:5]:
        print(f"    - {e}")
    print(f"\n  Output files:")
    print(f"    - {metrics_file}")
    print(f"    - {summary_file}")
    print(f"{'=' * 60}")
    
    return pipeline_summary


def run_benchmark(duration_seconds: int = 60, output_dir: str = "benchmark_results"):
    """
    Run System 1 benchmark for specified duration (Linux only).
    
    Args:
        duration_seconds: How long to run the benchmark
        output_dir: Directory for output files
    """
    if not WATCHDOG_AVAILABLE:
        print("[!] Error: watchdog module not available.")
        print("    This mode requires Linux with access to /var/log/")
        print("    Use --replay mode on Windows instead:")
        print("    python system1_bench.py --replay sample_logs.txt")
        return None
        
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    print(f"=" * 60)
    print(f"  System 1 Benchmark - LIVE MODE ({duration_seconds}s)")
    print(f"=" * 60)
    print(f"  Log files: {LOG_FILES}")
    print(f"  Output: {output_path.absolute()}")
    print(f"=" * 60)
    
    watchdog = InstrumentedWatchdog()
    watchdog.start_stream()
    
    metrics_file = output_path / "system1_metrics.csv"
    csv_file = open(metrics_file, 'w', newline='')
    writer = csv.DictWriter(csv_file, fieldnames=[
        'timestamp', 'raw_lines', 'noise_filtered', 'trust_filtered',
        'parse_failed', 'events_output', 'cpu_percent', 'memory_mb', 'parse_latency_us'
    ])
    writer.writeheader()
    
    start_time = time.time()
    sample_interval = 1.0
    last_sample = start_time
    
    total_metrics = PipelineMetrics(timestamp=datetime.now().isoformat())
    
    print(f"\n[*] Monitoring started at {datetime.now().strftime('%H:%M:%S')}")
    print(f"[*] Press Ctrl+C to stop early\n")
    
    try:
        while (time.time() - start_time) < duration_seconds:
            event = watchdog.read_stream_instrumented()
            
            if time.time() - last_sample >= sample_interval:
                metrics = watchdog.get_metrics()
                writer.writerow(asdict(metrics))
                csv_file.flush()
                
                total_metrics.raw_lines += metrics.raw_lines
                total_metrics.noise_filtered += metrics.noise_filtered
                total_metrics.trust_filtered += metrics.trust_filtered
                total_metrics.parse_failed += metrics.parse_failed
                total_metrics.events_output += metrics.events_output
                
                elapsed = int(time.time() - start_time)
                print(f"  [{elapsed:3d}s] raw={metrics.raw_lines:4d} "
                      f"noise={metrics.noise_filtered:4d} "
                      f"trust={metrics.trust_filtered:4d} "
                      f"out={metrics.events_output:4d} "
                      f"CPU={metrics.cpu_percent:5.1f}% "
                      f"RSS={metrics.memory_mb:5.1f}MB")
                
                watchdog.reset_metrics()
                last_sample = time.time()
                
    except KeyboardInterrupt:
        print("\n[!] Benchmark interrupted")
    finally:
        csv_file.close()
        watchdog.stop_stream()
    
    total_input = total_metrics.raw_lines or 1
    pipeline_summary = {
        "mode": "live",
        "duration_seconds": int(time.time() - start_time),
        "totals": {
            "raw_lines": total_metrics.raw_lines,
            "noise_filtered": total_metrics.noise_filtered,
            "trust_filtered": total_metrics.trust_filtered,
            "parse_failed": total_metrics.parse_failed,
            "events_output": total_metrics.events_output
        },
        "reduction_ratios": {
            "noise_gate": total_metrics.noise_filtered / total_input,
            "trust_filter": total_metrics.trust_filtered / total_input,
            "parse_fail": total_metrics.parse_failed / total_input,
            "total_reduction": 1 - (total_metrics.events_output / total_input)
        },
        "throughput": {
            "events_per_second": total_metrics.events_output / max(0.001, time.time() - start_time)
        }
    }
    
    summary_file = output_path / "system1_pipeline.json"
    with open(summary_file, 'w') as f:
        json.dump(pipeline_summary, f, indent=2)
    
    print(f"\n{'=' * 60}")
    print(f"  RESULTS SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total raw lines:     {total_metrics.raw_lines:,}")
    print(f"  Noise filtered:      {total_metrics.noise_filtered:,} ({pipeline_summary['reduction_ratios']['noise_gate']:.1%})")
    print(f"  Trust filtered:      {total_metrics.trust_filtered:,} ({pipeline_summary['reduction_ratios']['trust_filter']:.1%})")
    print(f"  Parse failures:      {total_metrics.parse_failed:,}")
    print(f"  Events output:       {total_metrics.events_output:,}")
    print(f"  Total reduction:     {pipeline_summary['reduction_ratios']['total_reduction']:.1%}")
    print(f"  Throughput:          {pipeline_summary['throughput']['events_per_second']:.1f} events/sec")
    print(f"\n  Output files:")
    print(f"    - {metrics_file}")
    print(f"    - {summary_file}")
    print(f"{'=' * 60}")
    
    return pipeline_summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="System 1 Benchmark (supports Windows replay mode)")
    parser.add_argument("--replay", "-r", type=str, default=None, 
                        help="Replay mode: path to log file (Windows compatible)")
    parser.add_argument("--duration", "-d", type=int, default=60, 
                        help="Duration in seconds (live mode only)")
    parser.add_argument("--output", "-o", type=str, default="benchmark_results", 
                        help="Output directory")
    args = parser.parse_args()
    
    if args.replay:
        # Windows-compatible replay mode
        run_benchmark_replay(log_file=args.replay, output_dir=args.output)
    else:
        # Linux live mode
        run_benchmark(duration_seconds=args.duration, output_dir=args.output)

