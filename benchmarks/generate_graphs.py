# benchmarks/generate_graphs.py
"""
Graph Generation for IEEE Paper Figures
Generates publication-ready figures from benchmark CSV/JSON data.

Requires: pip install matplotlib numpy pandas

Outputs (in benchmark_results/figures/):
- fig_a_volume_reduction.png: Stacked bar chart of System 1 pipeline stages
- fig_b_latency_cdf.png: System 2 latency CDF with P50/P90/P99 markers
- fig_c_e2e_breakdown.png: End-to-end timing stacked bars
- fig_d_agentic_timing.png: Agentic loop box plots
"""
import json
import sys
from pathlib import Path

try:
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
    import pandas as pd
except ImportError:
    print("Required packages not found. Install with:")
    print("  pip install matplotlib numpy pandas")
    sys.exit(1)

# IEEE-style settings
plt.rcParams.update({
    'font.size': 10,
    'font.family': 'serif',
    'figure.figsize': (6, 4),
    'figure.dpi': 150,
    'axes.labelsize': 10,
    'axes.titlesize': 11,
    'legend.fontsize': 9,
    'xtick.labelsize': 9,
    'ytick.labelsize': 9,
    'lines.linewidth': 1.5,
    'axes.grid': True,
    'grid.alpha': 0.3,
})


def fig_a_volume_reduction(data_dir: str, output_dir: str):
    """
    Figure A: System 1 volume reduction by pipeline stage.
    Stacked bar chart showing how events are filtered at each stage.
    """
    pipeline_file = Path(data_dir) / "system1_pipeline.json"
    if not pipeline_file.exists():
        print(f"  [!] {pipeline_file} not found - skipping Fig A")
        return
    
    with open(pipeline_file) as f:
        data = json.load(f)
    
    totals = data["totals"]
    
    # Calculate counts at each stage
    raw = totals["raw_lines"]
    after_noise = raw - totals["noise_filtered"]
    after_trust = after_noise - totals["trust_filtered"]
    after_parse = after_trust - totals["parse_failed"]
    output = totals["events_output"]
    
    # Data for stacked bar
    stages = ['Raw\nInput', 'After\nNoise Gate', 'After\nTrust Filter', 'After\nParsing', 'Final\nOutput']
    values = [raw, after_noise, after_trust, after_parse, output]
    
    fig, ax = plt.subplots(figsize=(7, 4))
    
    bars = ax.bar(stages, values, color=['#e74c3c', '#f39c12', '#3498db', '#2ecc71', '#27ae60'],
                  edgecolor='black', linewidth=0.5)
    
    # Add reduction percentages
    for i, (bar, val) in enumerate(zip(bars, values)):
        pct = (val / raw) * 100
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + raw*0.02,
                f'{val:,}\n({pct:.1f}%)', ha='center', va='bottom', fontsize=8)
    
    ax.set_ylabel('Event Count')
    ax.set_title('System 1 Pipeline: Volume Reduction by Stage')
    ax.set_ylim(0, raw * 1.2)
    
    # Add reduction ratio annotation
    reduction = data["reduction_ratios"]["total_reduction"]
    ax.annotate(f'Total Reduction: {reduction:.1%}',
                xy=(0.98, 0.95), xycoords='axes fraction',
                ha='right', va='top', fontsize=10,
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))
    
    plt.tight_layout()
    output_path = Path(output_dir) / "fig_a_volume_reduction.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  [+] Saved {output_path}")


def fig_b_latency_cdf(data_dir: str, output_dir: str):
    """
    Figure B: System 2 latency CDF with percentile markers.
    """
    csv_file = Path(data_dir) / "system2_latency.csv"
    stats_file = Path(data_dir) / "system2_stats.json"
    
    if not csv_file.exists():
        print(f"  [!] {csv_file} not found - skipping Fig B")
        return
    
    df = pd.read_csv(csv_file)
    latencies = df[df['success'] == True]['latency_ms'].values
    
    with open(stats_file) as f:
        stats = json.load(f)
    
    # Sort for CDF
    sorted_lat = np.sort(latencies)
    cdf = np.arange(1, len(sorted_lat) + 1) / len(sorted_lat)
    
    fig, ax = plt.subplots(figsize=(7, 4))
    
    ax.plot(sorted_lat, cdf, 'b-', linewidth=2, label='Latency CDF')
    
    # Mark percentiles
    overall = stats["overall"]
    p50 = overall.get("overall_p50_ms", 0)
    p90 = overall.get("overall_p90_ms", 0)
    p99 = overall.get("overall_p99_ms", 0)
    
    ax.axhline(0.50, color='green', linestyle='--', alpha=0.7, linewidth=1)
    ax.axhline(0.90, color='orange', linestyle='--', alpha=0.7, linewidth=1)
    ax.axhline(0.99, color='red', linestyle='--', alpha=0.7, linewidth=1)
    
    ax.axvline(p50, color='green', linestyle=':', alpha=0.7)
    ax.axvline(p90, color='orange', linestyle=':', alpha=0.7)
    ax.axvline(p99, color='red', linestyle=':', alpha=0.7)
    
    # Annotations
    ax.annotate(f'P50={p50:.0f}ms', xy=(p50, 0.50), xytext=(p50+200, 0.45),
                fontsize=9, arrowprops=dict(arrowstyle='->', color='green'))
    ax.annotate(f'P90={p90:.0f}ms', xy=(p90, 0.90), xytext=(p90+200, 0.85),
                fontsize=9, arrowprops=dict(arrowstyle='->', color='orange'))
    ax.annotate(f'P99={p99:.0f}ms', xy=(p99, 0.99), xytext=(p99+200, 0.92),
                fontsize=9, arrowprops=dict(arrowstyle='->', color='red'))
    
    ax.set_xlabel('Inference Latency (ms)')
    ax.set_ylabel('Cumulative Probability')
    ax.set_title('System 2 LLM Inference Latency Distribution')
    ax.set_xlim(0, max(sorted_lat) * 1.1)
    ax.set_ylim(0, 1.02)
    
    plt.tight_layout()
    output_path = Path(output_dir) / "fig_b_latency_cdf.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  [+] Saved {output_path}")


def fig_b_latency_boxplot(data_dir: str, output_dir: str):
    """
    Figure B (alternate): Box plot of latency by batch size.
    """
    csv_file = Path(data_dir) / "system2_latency.csv"
    
    if not csv_file.exists():
        print(f"  [!] {csv_file} not found - skipping Fig B boxplot")
        return
    
    df = pd.read_csv(csv_file)
    df = df[df['success'] == True]
    
    fig, ax = plt.subplots(figsize=(7, 4))
    
    batch_sizes = sorted(df['batch_size'].unique())
    data = [df[df['batch_size'] == bs]['latency_ms'].values for bs in batch_sizes]
    
    bp = ax.boxplot(data, labels=[str(bs) for bs in batch_sizes], patch_artist=True)
    
    colors = plt.cm.Blues(np.linspace(0.3, 0.8, len(batch_sizes)))
    for patch, color in zip(bp['boxes'], colors):
        patch.set_facecolor(color)
    
    ax.set_xlabel('Batch Size (events)')
    ax.set_ylabel('Inference Latency (ms)')
    ax.set_title('System 2 Latency vs Batch Size')
    
    plt.tight_layout()
    output_path = Path(output_dir) / "fig_b_latency_boxplot.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  [+] Saved {output_path}")


def fig_c_e2e_breakdown(data_dir: str, output_dir: str):
    """
    Figure C: End-to-end timing breakdown (stacked bar) showing P50, P90, P99.
    """
    csv_file = Path(data_dir) / "e2e_timing.csv"
    
    if not csv_file.exists():
        print(f"  [!] {csv_file} not found - skipping Fig C")
        return
    
    df = pd.read_csv(csv_file)
    
    # Columns to compute percentiles for
    stage_map = [
        ('parse_ms', 'Parse'),
        ('batch_wait_ms', 'Batch Wait (τ)'),
        ('inference_ms', 'LLM Inference'),
        ('persist_ms', 'DB Persist')
    ]
    
    # Compute percentiles for each stage
    percentiles = {'P50': {}, 'P90': {}, 'P99': {}}
    for col, label in stage_map:
        if col in df.columns:
            values = df[col].dropna().values
            if len(values) > 0:
                sorted_vals = np.sort(values)
                n = len(sorted_vals)
                percentiles['P50'][label] = sorted_vals[int(n * 0.5)]
                percentiles['P90'][label] = sorted_vals[int(n * 0.9)]
                percentiles['P99'][label] = sorted_vals[min(int(n * 0.99), n-1)]
    
    if not percentiles['P50']:
        print(f"  [!] No timing data available - skipping Fig C")
        return
    
    # Get stage labels that have data
    stages = [label for col, label in stage_map if label in percentiles['P50']]
    colors = ['#3498db', '#f39c12', '#9b59b6', '#2ecc71']
    
    fig, ax = plt.subplots(figsize=(6, 4))
    
    y_positions = [2, 1, 0]  # P50 at top, P99 at bottom
    y_labels = ['P50', 'P90', 'P99']
    bar_height = 0.6
    
    for y_idx, (y_pos, pct_label) in enumerate(zip(y_positions, y_labels)):
        left = 0
        pct_data = percentiles[pct_label]
        total = sum(pct_data.get(s, 0) for s in stages)
        
        for i, stage in enumerate(stages):
            val = pct_data.get(stage, 0)
            ax.barh(y_pos, val, left=left, height=bar_height, 
                   color=colors[i % len(colors)],
                   edgecolor='black', linewidth=0.5)
            left += val
        
        # Add total label at end of bar
        ax.text(left + 50, y_pos, f'{total:.0f}ms', va='center', fontsize=9, fontweight='bold')
    
    ax.set_yticks(y_positions)
    ax.set_yticklabels(y_labels)
    ax.set_xlabel('Time (ms)')
    ax.set_title('End-to-End Latency Breakdown by Percentile')
    
    # Create legend
    legend_patches = [mpatches.Patch(color=colors[i], label=stage) 
                      for i, stage in enumerate(stages)]
    ax.legend(handles=legend_patches, loc='upper right', fontsize=8)
    
    # Set x limit with some padding
    max_total = max(sum(percentiles[p].get(s, 0) for s in stages) for p in ['P50', 'P90', 'P99'])
    ax.set_xlim(0, max_total * 1.15)
    
    plt.tight_layout()
    output_path = Path(output_dir) / "fig_c_e2e_breakdown.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  [+] Saved {output_path}")



def fig_d_agentic_timing(data_dir: str, output_dir: str):
    """
    Figure D: Agentic loop timing (query→proposal, execution).
    """
    csv_file = Path(data_dir) / "agentic_timing.csv"
    
    if not csv_file.exists():
        print(f"  [!] {csv_file} not found - skipping Fig D")
        return
    
    df = pd.read_csv(csv_file)
    
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    
    # Left: Query to response by type
    ax1 = axes[0]
    proposal_times = df[df['response_type'] == 'proposal']['query_to_response_ms']
    text_times = df[df['response_type'] == 'text']['query_to_response_ms']
    
    data_to_plot = []
    labels = []
    if len(proposal_times) > 0:
        data_to_plot.append(proposal_times)
        labels.append(f'Proposal\n(n={len(proposal_times)})')
    if len(text_times) > 0:
        data_to_plot.append(text_times)
        labels.append(f'Text\n(n={len(text_times)})')
    
    if data_to_plot:
        bp = ax1.boxplot(data_to_plot, labels=labels, patch_artist=True)
        colors = ['#3498db', '#2ecc71']
        for patch, color in zip(bp['boxes'], colors[:len(data_to_plot)]):
            patch.set_facecolor(color)
    
    ax1.set_ylabel('Latency (ms)')
    ax1.set_title('Query → Response Latency')
    
    # Right: Execution timing (if available)
    ax2 = axes[1]
    exec_times = df[df['approval_to_first_byte_ms'].notna()]['approval_to_first_byte_ms']
    
    if len(exec_times) > 0:
        ax2.boxplot([exec_times], labels=[f'TTFB\n(n={len(exec_times)})'], patch_artist=True)
        ax2.set_ylabel('Time (ms)')
        ax2.set_title('Approval → First Output Byte')
    else:
        ax2.text(0.5, 0.5, 'No execution data\n(run with --execute)', 
                ha='center', va='center', transform=ax2.transAxes, fontsize=10)
        ax2.set_title('Execution Timing')
    
    plt.tight_layout()
    output_path = Path(output_dir) / "fig_d_agentic_timing.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  [+] Saved {output_path}")


def generate_all(data_dir: str = "benchmark_results"):
    """Generate all figures from benchmark data."""
    output_dir = Path(data_dir) / "figures"
    output_dir.mkdir(exist_ok=True)
    
    print(f"=" * 60)
    print(f"  Generating IEEE Paper Figures")
    print(f"=" * 60)
    print(f"  Data source: {data_dir}")
    print(f"  Output: {output_dir}")
    print(f"=" * 60)
    print()
    
    fig_a_volume_reduction(data_dir, output_dir)
    fig_b_latency_cdf(data_dir, output_dir)
    fig_b_latency_boxplot(data_dir, output_dir)
    fig_c_e2e_breakdown(data_dir, output_dir)
    fig_d_agentic_timing(data_dir, output_dir)
    
    print()
    print(f"=" * 60)
    print(f"  Done! Check {output_dir} for figures.")
    print(f"=" * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate IEEE paper figures")
    parser.add_argument("--data", "-d", type=str, default="benchmark_results", help="Data directory")
    args = parser.parse_args()
    
    generate_all(args.data)
