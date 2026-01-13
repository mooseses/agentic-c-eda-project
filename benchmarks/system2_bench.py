# benchmarks/system2_bench.py
"""
System 2 Benchmark: LLM Inference Latency Distribution
Measures reasoning engine performance across batch sizes.

Outputs:
- system2_latency.csv: Per-batch latency measurements
- system2_stats.json: P50/P90/P99 statistics and failure rates
"""
import os
import sys
import time
import json
import csv
import statistics
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from logic import ReasoningEngine
from database import get_db


# Sample events for benchmarking (realistic normalized format)
SAMPLE_EVENTS = [
    "SSH_AUTH_FAIL User=root Source=185.143.223.47 Method=password",
    "SSH_AUTH_FAIL User=admin Source=185.143.223.47 Method=password",
    "SSH_AUTH_FAIL User=ubuntu Source=185.143.223.47 Method=password",
    "NET_CONN Source=192.168.1.100 Port=22 Proto=TCP",
    "NET_CONN Source=10.0.0.50 Port=8080 Proto=TCP",
    "SSH_AUTH_SUCCESS User=petej Source=10.0.0.5 Method=key",
    "SUDO_EXEC User=petej Session=SSH TTY=pts/0 Command=/usr/bin/apt update",
    "SESSION_OPEN Service=sshd User=petej",
    "NET_PING Source=192.168.1.1",
    "SSH_INVALID_USER User=test Source=45.33.32.156",
    "NET_CONN Source=185.143.223.47 Port=3389 Proto=TCP",
    "NET_CONN Source=185.143.223.47 Port=445 Proto=TCP",
    "NET_CONN Source=185.143.223.47 Port=139 Proto=TCP",
    "SUDO_AUTH_FAIL User=unknown Session=LOCAL TTY=tty1",
    "SSH_CONNECTION_CLOSED User=petej Source=10.0.0.5",
]


@dataclass
class LatencyMeasurement:
    """Single latency measurement."""
    timestamp: str
    batch_size: int
    latency_ms: float
    success: bool
    flagged: Optional[bool] = None
    severity: Optional[str] = None
    error: Optional[str] = None


def run_benchmark(
    iterations: int = 50,
    batch_sizes: list = None,
    output_dir: str = "benchmark_results",
    db_path: str = None
):
    """
    Run System 2 latency benchmark.
    
    Args:
        iterations: Number of iterations per batch size
        batch_sizes: List of batch sizes to test
        output_dir: Directory for output files
        db_path: Optional database path for config
    """
    if batch_sizes is None:
        batch_sizes = [1, 3, 5, 10, 15, 20]
    
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    print(f"=" * 60)
    print(f"  System 2 Benchmark - LLM Inference Latency")
    print(f"=" * 60)
    print(f"  Batch sizes: {batch_sizes}")
    print(f"  Iterations per size: {iterations}")
    print(f"  Total calls: {len(batch_sizes) * iterations}")
    print(f"=" * 60)
    
    # Initialize reasoning engine
    db = get_db(db_path) if db_path else None
    engine = ReasoningEngine(db=db)
    
    # Get LLM config for display
    if db:
        api_url = db.get_config("llm_api_url", "http://localhost:1234/v1/chat/completions")
        model = db.get_config("llm_model", "qwen/qwen3-4b-2507")
    else:
        from config import LLM_API_URL, LLM_MODEL
        api_url = LLM_API_URL
        model = LLM_MODEL
    
    print(f"  LLM API: {api_url}")
    print(f"  Model: {model}")
    print(f"=" * 60)
    
    # Open CSV file
    csv_file_path = output_path / "system2_latency.csv"
    csv_file = open(csv_file_path, 'w', newline='')
    writer = csv.DictWriter(csv_file, fieldnames=[
        'timestamp', 'batch_size', 'latency_ms', 'success', 'flagged', 'severity', 'error'
    ])
    writer.writeheader()
    
    all_measurements = []
    stats_by_size = {}
    
    print(f"\n[*] Starting benchmark at {datetime.now().strftime('%H:%M:%S')}\n")
    
    for batch_size in batch_sizes:
        print(f"  Testing batch_size={batch_size}...")
        measurements = []
        
        for i in range(iterations):
            # Create batch of specified size
            batch = SAMPLE_EVENTS[:batch_size] if batch_size <= len(SAMPLE_EVENTS) else (
                SAMPLE_EVENTS * ((batch_size // len(SAMPLE_EVENTS)) + 1)
            )[:batch_size]
            
            # Measure inference time
            start = time.perf_counter()
            try:
                result = engine.analyze_batch(batch)
                latency_ms = (time.perf_counter() - start) * 1000
                
                measurement = LatencyMeasurement(
                    timestamp=datetime.now().isoformat(),
                    batch_size=batch_size,
                    latency_ms=latency_ms,
                    success=True,
                    flagged=result.get('flagged'),
                    severity=result.get('severity')
                )
            except Exception as e:
                latency_ms = (time.perf_counter() - start) * 1000
                measurement = LatencyMeasurement(
                    timestamp=datetime.now().isoformat(),
                    batch_size=batch_size,
                    latency_ms=latency_ms,
                    success=False,
                    error=str(e)
                )
            
            measurements.append(measurement)
            writer.writerow(asdict(measurement))
            csv_file.flush()
            
            # Progress indicator
            if (i + 1) % 10 == 0:
                print(f"    [{i+1}/{iterations}] last={latency_ms:.0f}ms")
        
        # Calculate statistics for this batch size
        latencies = [m.latency_ms for m in measurements if m.success]
        failures = [m for m in measurements if not m.success]
        
        if latencies:
            sorted_lat = sorted(latencies)
            stats_by_size[batch_size] = {
                "count": len(latencies),
                "failures": len(failures),
                "failure_rate": len(failures) / len(measurements),
                "min_ms": min(latencies),
                "max_ms": max(latencies),
                "mean_ms": statistics.mean(latencies),
                "median_ms": statistics.median(latencies),
                "stdev_ms": statistics.stdev(latencies) if len(latencies) > 1 else 0,
                "p50_ms": sorted_lat[int(len(sorted_lat) * 0.50)],
                "p90_ms": sorted_lat[int(len(sorted_lat) * 0.90)],
                "p99_ms": sorted_lat[int(len(sorted_lat) * 0.99)] if len(sorted_lat) >= 100 else sorted_lat[-1],
            }
        else:
            stats_by_size[batch_size] = {"count": 0, "failures": len(failures), "failure_rate": 1.0}
        
        all_measurements.extend(measurements)
        print(f"    Done: P50={stats_by_size[batch_size].get('p50_ms', 0):.0f}ms "
              f"P90={stats_by_size[batch_size].get('p90_ms', 0):.0f}ms "
              f"failures={len(failures)}")
    
    csv_file.close()
    
    # Overall statistics
    all_latencies = [m.latency_ms for m in all_measurements if m.success]
    all_failures = [m for m in all_measurements if not m.success]
    
    if all_latencies:
        sorted_all = sorted(all_latencies)
        overall_stats = {
            "total_calls": len(all_measurements),
            "successful": len(all_latencies),
            "failed": len(all_failures),
            "failure_rate": len(all_failures) / len(all_measurements),
            "overall_p50_ms": sorted_all[int(len(sorted_all) * 0.50)],
            "overall_p90_ms": sorted_all[int(len(sorted_all) * 0.90)],
            "overall_p99_ms": sorted_all[int(len(sorted_all) * 0.99)] if len(sorted_all) >= 100 else sorted_all[-1],
            "overall_mean_ms": statistics.mean(all_latencies),
        }
    else:
        overall_stats = {"total_calls": len(all_measurements), "failed": len(all_failures), "failure_rate": 1.0}
    
    # Save statistics
    stats_output = {
        "config": {
            "api_url": api_url,
            "model": model,
            "iterations_per_size": iterations,
            "batch_sizes": batch_sizes
        },
        "overall": overall_stats,
        "by_batch_size": stats_by_size
    }
    
    stats_file = output_path / "system2_stats.json"
    with open(stats_file, 'w') as f:
        json.dump(stats_output, f, indent=2)
    
    print(f"\n{'=' * 60}")
    print(f"  RESULTS SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total calls:    {overall_stats.get('total_calls', 0)}")
    print(f"  Successful:     {overall_stats.get('successful', 0)}")
    print(f"  Failed:         {overall_stats.get('failed', 0)} ({overall_stats.get('failure_rate', 0):.1%})")
    print(f"\n  Overall Latency:")
    print(f"    P50:  {overall_stats.get('overall_p50_ms', 0):,.0f} ms")
    print(f"    P90:  {overall_stats.get('overall_p90_ms', 0):,.0f} ms")
    print(f"    P99:  {overall_stats.get('overall_p99_ms', 0):,.0f} ms")
    print(f"    Mean: {overall_stats.get('overall_mean_ms', 0):,.0f} ms")
    print(f"\n  By Batch Size:")
    for bs, st in stats_by_size.items():
        print(f"    {bs:2d} events: P50={st.get('p50_ms', 0):,.0f}ms  P90={st.get('p90_ms', 0):,.0f}ms")
    print(f"\n  Output files:")
    print(f"    - {csv_file_path}")
    print(f"    - {stats_file}")
    print(f"{'=' * 60}")
    
    return stats_output


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="System 2 Latency Benchmark")
    parser.add_argument("--iterations", "-n", type=int, default=50, help="Iterations per batch size")
    parser.add_argument("--batch-sizes", "-b", type=str, default="1,3,5,10,15,20", 
                        help="Comma-separated batch sizes")
    parser.add_argument("--output", "-o", type=str, default="benchmark_results", help="Output directory")
    parser.add_argument("--db", type=str, default=None, help="Database path")
    args = parser.parse_args()
    
    batch_sizes = [int(x) for x in args.batch_sizes.split(",")]
    run_benchmark(
        iterations=args.iterations,
        batch_sizes=batch_sizes,
        output_dir=args.output,
        db_path=args.db
    )
