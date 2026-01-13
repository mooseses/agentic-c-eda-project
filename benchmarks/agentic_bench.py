# benchmarks/agentic_bench.py
"""
Agentic Loop Benchmark: Query-to-Proposal and Execution Timing
Measures chat agent responsiveness and command execution latency.

Outputs:
- agentic_timing.csv: Per-interaction timing data
- agentic_stats.json: Summary statistics
"""
import os
import sys
import time
import json
import csv
import requests
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))


@dataclass
class AgenticMeasurement:
    """Timing for one agentic interaction."""
    timestamp: str
    query: str
    query_to_response_ms: float
    response_type: str  # text, proposal, error
    proposal_command: Optional[str] = None
    approval_to_first_byte_ms: Optional[float] = None
    execution_duration_ms: Optional[float] = None
    analysis_duration_ms: Optional[float] = None
    

# Test queries that should trigger different responses
TEST_QUERIES = [
    # Should trigger propose_command
    {"query": "What ports are listening?", "expected_tool": "propose_command"},
    {"query": "Show me failed SSH logins", "expected_tool": "propose_command"},
    {"query": "Check if port 22 is open", "expected_tool": "propose_command"},
    {"query": "Who is logged in right now?", "expected_tool": "propose_command"},
    {"query": "Show me the last 10 lines of auth.log", "expected_tool": "propose_command"},
    
    # Should trigger get_events/get_flags
    {"query": "Show me recent security events", "expected_tool": "get_events"},
    {"query": "Are there any pending flags?", "expected_tool": "get_flags"},
    
    # Conversational (no tool)
    {"query": "What is your purpose?", "expected_tool": None},
    {"query": "Explain what System 1 does", "expected_tool": None},
]


def measure_chat_response(
    api_url: str,
    api_key: str,
    query: str
) -> tuple[float, str, Optional[str]]:
    """
    Measure time from query submission to response.
    
    Returns:
        (latency_ms, response_type, proposal_command)
    """
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key
    }
    
    start = time.perf_counter()
    
    try:
        # Use streaming endpoint
        response = requests.post(
            f"{api_url}/api/chat",
            headers=headers,
            json={"message": query},
            stream=True,
            timeout=60
        )
        response.raise_for_status()
        
        response_type = "text"
        proposal_command = None
        first_byte = None
        
        for line in response.iter_lines():
            if first_byte is None:
                first_byte = time.perf_counter()
                
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    data = json.loads(line[6:])
                    event = data.get('event', '')
                    
                    if event == 'proposal':
                        response_type = 'proposal'
                        proposal_command = data.get('command', '')
                        break
                    elif event == 'text':
                        response_type = 'text'
                    elif event == 'done':
                        break
        
        latency_ms = (first_byte - start) * 1000 if first_byte else (time.perf_counter() - start) * 1000
        return (latency_ms, response_type, proposal_command)
        
    except Exception as e:
        latency_ms = (time.perf_counter() - start) * 1000
        return (latency_ms, 'error', str(e))


def measure_command_execution(
    api_url: str,
    api_key: str,
    command: str
) -> tuple[float, float]:
    """
    Measure command execution timing.
    
    Returns:
        (time_to_first_byte_ms, total_duration_ms)
    """
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key
    }
    
    start = time.perf_counter()
    first_byte = None
    
    try:
        response = requests.post(
            f"{api_url}/api/execute",
            headers=headers,
            json={"command": command},
            stream=True,
            timeout=60
        )
        response.raise_for_status()
        
        for line in response.iter_lines():
            if first_byte is None:
                first_byte = time.perf_counter()
            
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    data = json.loads(line[6:])
                    if data.get('event') == 'done':
                        break
        
        end = time.perf_counter()
        ttfb = (first_byte - start) * 1000 if first_byte else 0
        duration = (end - start) * 1000
        return (ttfb, duration)
        
    except Exception as e:
        return (0, (time.perf_counter() - start) * 1000)


def run_benchmark(
    api_url: str = "http://localhost:8000",
    api_key: str = None,
    iterations: int = 3,
    output_dir: str = "benchmark_results",
    execute_proposals: bool = False  # Set True to actually run commands
):
    """
    Run agentic loop benchmark.
    
    Args:
        api_url: Dashboard API URL
        api_key: API key for authentication
        iterations: Iterations per test query
        output_dir: Output directory
        execute_proposals: Whether to execute proposed commands
    """
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    if not api_key:
        api_key = os.environ.get("SENTINEL_API_KEY", "")
    
    print(f"=" * 60)
    print(f"  Agentic Loop Benchmark")
    print(f"=" * 60)
    print(f"  API URL: {api_url}")
    print(f"  Test queries: {len(TEST_QUERIES)}")
    print(f"  Iterations: {iterations}")
    print(f"  Execute proposals: {execute_proposals}")
    print(f"=" * 60)
    
    # Test connection
    try:
        resp = requests.get(f"{api_url}/api/health", timeout=5)
        if resp.status_code != 200:
            print(f"\n[!] API health check failed: {resp.status_code}")
            return
        print(f"\n[*] API health check: OK")
    except Exception as e:
        print(f"\n[!] Cannot connect to API: {e}")
        print(f"    Make sure the dashboard is running: python -m uvicorn web.api:app")
        return
    
    # Open CSV
    csv_path = output_path / "agentic_timing.csv"
    csv_file = open(csv_path, 'w', newline='')
    writer = csv.DictWriter(csv_file, fieldnames=[
        'timestamp', 'query', 'query_to_response_ms', 'response_type',
        'proposal_command', 'approval_to_first_byte_ms', 'execution_duration_ms',
        'analysis_duration_ms'
    ])
    writer.writeheader()
    
    measurements = []
    
    print(f"\n[*] Starting benchmark...\n")
    
    for query_info in TEST_QUERIES:
        query = query_info["query"]
        expected = query_info["expected_tool"]
        
        print(f"  Query: \"{query[:40]}...\"")
        
        for i in range(iterations):
            latency, rtype, proposal = measure_chat_response(api_url, api_key, query)
            
            m = AgenticMeasurement(
                timestamp=datetime.now().isoformat(),
                query=query,
                query_to_response_ms=latency,
                response_type=rtype,
                proposal_command=proposal
            )
            
            # If proposal and we're executing
            if rtype == 'proposal' and proposal and execute_proposals:
                # Only execute safe commands (handles sudo prefix)
                safe_commands = ['ss', 'who', 'last', 'ps', 'netstat', 'tail', 'cat', 'head', 'grep']
                cmd_to_check = proposal.strip()
                if cmd_to_check.startswith('sudo '):
                    cmd_to_check = cmd_to_check[5:].strip()
                is_safe = any(cmd_to_check.startswith(cmd) for cmd in safe_commands)
                
                if is_safe:
                    ttfb, duration = measure_command_execution(api_url, api_key, proposal)
                    m.approval_to_first_byte_ms = ttfb
                    m.execution_duration_ms = duration
            
            measurements.append(m)
            writer.writerow(asdict(m))
            csv_file.flush()
            
            print(f"    [{i+1}] {latency:.0f}ms -> {rtype}" + 
                  (f" ({proposal[:30]}...)" if proposal else ""))
            
            time.sleep(0.5)  # Rate limit
    
    csv_file.close()
    
    # Calculate statistics
    proposal_latencies = [m.query_to_response_ms for m in measurements if m.response_type == 'proposal']
    text_latencies = [m.query_to_response_ms for m in measurements if m.response_type == 'text']
    exec_ttfbs = [m.approval_to_first_byte_ms for m in measurements if m.approval_to_first_byte_ms]
    
    def percentile(values, p):
        if not values:
            return None
        sorted_v = sorted(values)
        idx = int(len(sorted_v) * p)
        return sorted_v[min(idx, len(sorted_v)-1)]
    
    stats = {
        "config": {
            "api_url": api_url,
            "iterations": iterations,
            "total_queries": len(measurements)
        },
        "query_to_proposal": {
            "count": len(proposal_latencies),
            "p50_ms": percentile(proposal_latencies, 0.5),
            "p90_ms": percentile(proposal_latencies, 0.9),
            "mean_ms": sum(proposal_latencies) / len(proposal_latencies) if proposal_latencies else None
        },
        "query_to_text": {
            "count": len(text_latencies),
            "p50_ms": percentile(text_latencies, 0.5),
            "p90_ms": percentile(text_latencies, 0.9),
            "mean_ms": sum(text_latencies) / len(text_latencies) if text_latencies else None
        },
        "execution_ttfb": {
            "count": len(exec_ttfbs),
            "p50_ms": percentile(exec_ttfbs, 0.5),
            "p90_ms": percentile(exec_ttfbs, 0.9),
        },
        "response_type_distribution": {
            "proposal": len([m for m in measurements if m.response_type == 'proposal']),
            "text": len([m for m in measurements if m.response_type == 'text']),
            "error": len([m for m in measurements if m.response_type == 'error'])
        }
    }
    
    stats_path = output_path / "agentic_stats.json"
    with open(stats_path, 'w') as f:
        json.dump(stats, f, indent=2)
    
    print(f"\n{'=' * 60}")
    print(f"  RESULTS")
    print(f"{'=' * 60}")
    print(f"  Total queries: {len(measurements)}")
    print(f"\n  Query → Proposal:")
    print(f"    Count: {stats['query_to_proposal']['count']}")
    print(f"    P50:   {stats['query_to_proposal']['p50_ms']:.0f} ms" if stats['query_to_proposal']['p50_ms'] else "    P50:   N/A")
    print(f"    P90:   {stats['query_to_proposal']['p90_ms']:.0f} ms" if stats['query_to_proposal']['p90_ms'] else "    P90:   N/A")
    print(f"\n  Query → Text Response:")
    print(f"    Count: {stats['query_to_text']['count']}")
    print(f"    P50:   {stats['query_to_text']['p50_ms']:.0f} ms" if stats['query_to_text']['p50_ms'] else "    P50:   N/A")
    print(f"\n  Response Distribution:")
    for rtype, count in stats['response_type_distribution'].items():
        print(f"    {rtype}: {count}")
    print(f"\n  Output files:")
    print(f"    - {csv_path}")
    print(f"    - {stats_path}")
    print(f"{'=' * 60}")
    
    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Agentic Loop Benchmark")
    parser.add_argument("--url", "-u", type=str, default="http://localhost:8000", help="API URL")
    parser.add_argument("--key", "-k", type=str, default=None, help="API key")
    parser.add_argument("--iterations", "-n", type=int, default=3, help="Iterations per query")
    parser.add_argument("--output", "-o", type=str, default="benchmark_results", help="Output dir")
    parser.add_argument("--execute", "-x", action="store_true", help="Execute safe proposals")
    args = parser.parse_args()
    
    run_benchmark(
        api_url=args.url,
        api_key=args.key,
        iterations=args.iterations,
        output_dir=args.output,
        execute_proposals=args.execute
    )
