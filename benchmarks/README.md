# C-EDA Benchmark Suite

## Quick Start

```bash
# Install graph dependencies (optional, only for figure generation)
pip install matplotlib numpy pandas

# Run benchmarks
cd benchmarks

# 1. System 1: Throughput & Volume Reduction (requires Linux with log files)
python system1_bench.py --duration 60

# 2. System 2: LLM Latency (requires LM Studio running on port 1234)
python system2_bench.py --iterations 20 --batch-sizes 1,5,10,20

# 3. End-to-End: Pipeline Timing (requires daemon setup)
python e2e_bench.py --attack ssh_brute --count 20

# 4. Agentic Loop: Chat Timing (requires dashboard running)
python agentic_bench.py --url http://localhost:8000 --key YOUR_API_KEY

# Generate figures from collected data
python generate_graphs.py --data benchmark_results
```

## Output Files

All benchmarks write to `benchmark_results/`:

| Benchmark | CSV File | JSON Stats |
|-----------|----------|------------|
| System 1 | `system1_metrics.csv` | `system1_pipeline.json` |
| System 2 | `system2_latency.csv` | `system2_stats.json` |
| End-to-End | `e2e_timing.csv` | `e2e_breakdown.json` |
| Agentic | `agentic_timing.csv` | `agentic_stats.json` |

## Generated Figures

After running `generate_graphs.py`, find figures in `benchmark_results/figures/`:

- **fig_a_volume_reduction.png**: Stacked bar chart of System 1 pipeline stages
- **fig_b_latency_cdf.png**: System 2 latency CDF with P50/P90/P99 markers
- **fig_b_latency_boxplot.png**: Latency vs batch size box plot
- **fig_c_e2e_breakdown.png**: End-to-end timing stacked bars
- **fig_d_agentic_timing.png**: Agentic loop timing box plots

## Prerequisites

### For System 1 Benchmark
- Linux system with `/var/log/syslog` and `/var/log/auth.log`
- Or configure `LOG_FILES` in `config.py` to point to test logs

### For System 2 Benchmark
- LLM server running (e.g., LM Studio on `http://localhost:1234`)
- Configure endpoint in database or `config.py`

### For Agentic Benchmark
- Dashboard running: `python -m uvicorn web.api:app --port 8000`
- API key from dashboard startup output

## Generating Real Traffic (Recommended)

Instead of synthetic log injection, generate real events:

```bash
# SSH brute force (from another machine)
for i in {1..10}; do
  ssh -o ConnectTimeout=1 baduser@target_ip 2>/dev/null
done

# Port scan
nmap -sT -p1-100 target_ip

# Failed sudo
sudo -k && sudo -S ls <<< "wrongpassword" 2>/dev/null
```

## Batch Size Selection

For System 2 benchmarks, recommended batch sizes:
- **1**: Single-event baseline
- **5**: Default batch window size
- **10-20**: Stress testing / burst scenarios

## Customization

Edit `TEST_QUERIES` in `agentic_bench.py` to test specific investigation flows.
