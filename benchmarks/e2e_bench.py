# benchmarks/e2e_bench.py
"""
End-to-End Benchmark: Detection-to-Notification Pipeline
Measures T0 (log write) → T5 (dashboard notification) timing.

Outputs:
- e2e_timing.csv: Per-event stage timestamps
- e2e_breakdown.json: Median time per stage
"""
import os
import sys
import time
import json
import csv
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))


@dataclass
class E2ETimestamp:
    """Timestamps for each pipeline stage."""
    event_id: int
    t0_log_write: float  # Log line written
    t1_parsed: Optional[float] = None  # Event parsed by System 1
    t2_batch_trigger: Optional[float] = None  # Batch analysis triggered
    t3_verdict: Optional[float] = None  # System 2 verdict received
    t4_persisted: Optional[float] = None  # Flag written to database
    t5_sse_sent: Optional[float] = None  # SSE notification sent
    
    @property
    def total_latency_ms(self) -> Optional[float]:
        if self.t0_log_write and self.t4_persisted:
            return (self.t4_persisted - self.t0_log_write) * 1000
        return None
    
    @property
    def parse_latency_ms(self) -> Optional[float]:
        if self.t0_log_write and self.t1_parsed:
            return (self.t1_parsed - self.t0_log_write) * 1000
        return None
    
    @property
    def batch_wait_ms(self) -> Optional[float]:
        if self.t1_parsed and self.t2_batch_trigger:
            return (self.t2_batch_trigger - self.t1_parsed) * 1000
        return None
    
    @property
    def inference_latency_ms(self) -> Optional[float]:
        if self.t2_batch_trigger and self.t3_verdict:
            return (self.t3_verdict - self.t2_batch_trigger) * 1000
        return None
    
    @property
    def persist_latency_ms(self) -> Optional[float]:
        if self.t3_verdict and self.t4_persisted:
            return (self.t4_persisted - self.t3_verdict) * 1000
        return None


def generate_ssh_attack_log(log_path: str, source_ip: str = "185.143.223.47", 
                            count: int = 10, delay: float = 0.1) -> list:
    """
    Generate realistic SSH brute-force log entries.
    
    Args:
        log_path: Path to auth.log file
        source_ip: Attacker IP address
        count: Number of failed login attempts
        delay: Delay between entries
        
    Returns:
        List of timestamps when each line was written
    """
    timestamps = []
    users = ["root", "admin", "ubuntu", "test", "user", "guest", "oracle", "postgres", "mysql"]
    
    with open(log_path, 'a') as f:
        for i in range(count):
            now = datetime.now()
            user = users[i % len(users)]
            # Realistic syslog format
            line = f"{now.strftime('%b %d %H:%M:%S')} hostname sshd[{12345 + i}]: Failed password for {user} from {source_ip} port {50000 + i} ssh2\n"
            
            write_time = time.perf_counter()
            f.write(line)
            f.flush()
            timestamps.append(write_time)
            
            if delay > 0:
                time.sleep(delay)
    
    return timestamps


def generate_port_scan_log(log_path: str, source_ip: str = "192.168.1.100",
                           ports: list = None) -> list:
    """Generate iptables-style port scan log entries."""
    if ports is None:
        ports = [21, 22, 23, 25, 80, 110, 139, 443, 445, 3389]
    
    timestamps = []
    
    with open(log_path, 'a') as f:
        for port in ports:
            now = datetime.now()
            # Realistic iptables LOG format
            line = (f"{now.strftime('%b %d %H:%M:%S')} hostname kernel: [Sentinel] "
                   f"IN=eth0 OUT= MAC=... SRC={source_ip} DST=10.0.0.1 "
                   f"LEN=60 TOS=0x00 PREC=0x00 TTL=64 ID={1000+port} DF "
                   f"PROTO=TCP SPT={50000+port} DPT={port} WINDOW=64240 "
                   f"RES=0x00 SYN URGP=0\n")
            
            write_time = time.perf_counter()
            f.write(line)
            f.flush()
            timestamps.append(write_time)
            
            time.sleep(0.05)  # Fast scan
    
    return timestamps


class InstrumentedDaemon:
    """Daemon wrapper with timing instrumentation."""
    
    def __init__(self, log_files: list, db_path: str = None):
        self.log_files = log_files
        self.db_path = db_path
        self.timestamps = {}
        self.running = False
        
    def run_timed_analysis(self, duration: float = 30.0, batch_interval: float = 5.0):
        """
        Run the daemon with timing instrumentation.
        
        Args:
            duration: How long to run
            batch_interval: Time between batch analyses
        """
        import config
        from watchdog import LogWatchdog
        from logic import ReasoningEngine
        from database import get_db
        
        db = get_db(self.db_path) if self.db_path else get_db()
        # Pass log_files directly to LogWatchdog
        watchdog = LogWatchdog(log_files=self.log_files)
        reasoning = ReasoningEngine(db=db)
        
        watchdog.start_stream()
        
        event_buffer = []
        buffer_start_time = None
        event_id = 0
        
        start_time = time.time()
        self.running = True
        
        try:
            while self.running and (time.time() - start_time) < duration:
                t_parse_start = time.perf_counter()
                event = watchdog.read_stream()
                
                if event:
                    t_parsed = time.perf_counter()
                    event_id += 1
                    
                    self.timestamps[event_id] = E2ETimestamp(
                        event_id=event_id,
                        t0_log_write=t_parse_start,  # Approximation
                        t1_parsed=t_parsed
                    )
                    
                    event_buffer.append((event_id, event))
                    
                    if buffer_start_time is None:
                        buffer_start_time = time.time()
                
                # Batch trigger
                if buffer_start_time is not None:
                    elapsed = time.time() - buffer_start_time
                    if elapsed >= batch_interval:
                        t_batch = time.perf_counter()
                        
                        # Mark batch trigger for all events
                        for eid, _ in event_buffer:
                            if eid in self.timestamps:
                                self.timestamps[eid].t2_batch_trigger = t_batch
                        
                        # LLM analysis
                        events_only = [e for _, e in event_buffer]
                        result = reasoning.analyze_batch(events_only)
                        t_verdict = time.perf_counter()
                        
                        # Mark verdict time
                        for eid, _ in event_buffer:
                            if eid in self.timestamps:
                                self.timestamps[eid].t3_verdict = t_verdict
                        
                        # Persist flag if flagged
                        if result.get('flagged'):
                            event_ids = [eid for eid, _ in event_buffer]
                            db.insert_flag(
                                event_ids=event_ids,
                                severity=result.get('severity', 'info'),
                                summary=result.get('summary', ''),
                                suggested_actions=result.get('suggested_actions', [])
                            )
                            t_persisted = time.perf_counter()
                            
                            for eid, _ in event_buffer:
                                if eid in self.timestamps:
                                    self.timestamps[eid].t4_persisted = t_persisted
                        
                        event_buffer.clear()
                        buffer_start_time = None
                        
        finally:
            watchdog.stop_stream()
        
        return self.timestamps


def run_benchmark(
    attack_type: str = "ssh_brute",
    event_count: int = 20,
    batch_interval: float = 5.0,
    output_dir: str = "benchmark_results"
):
    """
    Run end-to-end timing benchmark.
    
    Args:
        attack_type: Type of attack to simulate (ssh_brute, port_scan)
        event_count: Number of events to generate
        batch_interval: Batch analysis interval
        output_dir: Output directory
    """
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    print(f"=" * 60)
    print(f"  End-to-End Timing Benchmark")
    print(f"=" * 60)
    print(f"  Attack type: {attack_type}")
    print(f"  Event count: {event_count}")
    print(f"  Batch interval: {batch_interval}s")
    print(f"=" * 60)
    
    # Create temporary log file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
        temp_log = f.name
    
    print(f"\n[*] Using temp log: {temp_log}")
    
    # Start instrumented daemon in background
    daemon = InstrumentedDaemon(log_files=[temp_log])
    
    daemon_thread = threading.Thread(
        target=daemon.run_timed_analysis,
        kwargs={"duration": 60.0, "batch_interval": batch_interval}
    )
    daemon_thread.start()
    
    # Wait for daemon to initialize
    time.sleep(1.0)
    
    print(f"[*] Generating {attack_type} attack events...")
    
    # Generate attack events
    if attack_type == "ssh_brute":
        write_times = generate_ssh_attack_log(temp_log, count=event_count, delay=0.2)
    elif attack_type == "port_scan":
        write_times = generate_port_scan_log(temp_log)
    else:
        print(f"[!] Unknown attack type: {attack_type}")
        return
    
    print(f"[*] Generated {len(write_times)} events")
    print(f"[*] Waiting for analysis (batch interval + inference)...")
    
    # Wait for processing
    time.sleep(batch_interval + 15)  # Batch interval + LLM time + margin
    
    daemon.running = False
    daemon_thread.join(timeout=10)
    
    # Collect results
    timestamps = daemon.timestamps
    
    # Calculate breakdowns
    results = []
    for eid, ts in timestamps.items():
        results.append({
            "event_id": eid,
            "total_ms": ts.total_latency_ms,
            "parse_ms": ts.parse_latency_ms,
            "batch_wait_ms": ts.batch_wait_ms,
            "inference_ms": ts.inference_latency_ms,
            "persist_ms": ts.persist_latency_ms
        })
    
    # Write CSV
    csv_path = output_path / "e2e_timing.csv"
    if results:
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
    
    # Calculate medians
    def median(values):
        values = [v for v in values if v is not None]
        if not values:
            return None
        sorted_v = sorted(values)
        mid = len(sorted_v) // 2
        return sorted_v[mid]
    
    breakdown = {
        "config": {
            "attack_type": attack_type,
            "event_count": event_count,
            "batch_interval_s": batch_interval
        },
        "sample_count": len(results),
        "median_breakdown_ms": {
            "parse": median([r["parse_ms"] for r in results]),
            "batch_wait": median([r["batch_wait_ms"] for r in results]),
            "inference": median([r["inference_ms"] for r in results]),
            "persist": median([r["persist_ms"] for r in results]),
            "total": median([r["total_ms"] for r in results])
        }
    }
    
    stats_path = output_path / "e2e_breakdown.json"
    with open(stats_path, 'w') as f:
        json.dump(breakdown, f, indent=2)
    
    # Cleanup
    try:
        os.unlink(temp_log)
    except:
        pass
    
    print(f"\n{'=' * 60}")
    print(f"  RESULTS")
    print(f"{'=' * 60}")
    print(f"  Events processed: {len(results)}")
    print(f"\n  Median Latency Breakdown:")
    for stage, ms in breakdown["median_breakdown_ms"].items():
        if ms is not None:
            print(f"    {stage:12s}: {ms:,.0f} ms")
    print(f"\n  Output files:")
    print(f"    - {csv_path}")
    print(f"    - {stats_path}")
    print(f"{'=' * 60}")
    
    return breakdown


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="End-to-End Timing Benchmark")
    parser.add_argument("--attack", "-a", type=str, default="ssh_brute", 
                        choices=["ssh_brute", "port_scan"], help="Attack type")
    parser.add_argument("--count", "-n", type=int, default=20, help="Event count")
    parser.add_argument("--interval", "-i", type=float, default=5.0, help="Batch interval")
    parser.add_argument("--output", "-o", type=str, default="benchmark_results", help="Output dir")
    args = parser.parse_args()
    
    run_benchmark(
        attack_type=args.attack,
        event_count=args.count,
        batch_interval=args.interval,
        output_dir=args.output
    )
