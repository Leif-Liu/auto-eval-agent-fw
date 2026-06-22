"""Chart generation — radar chart and trend chart using matplotlib."""

from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt
import numpy as np

from src.models.evaluation_result import DimensionScore, CompositeResult
from src.models.monitoring import DimensionSlope, MaturityStep


def _save_figure(fig: plt.Figure, output_path: Path | None, default_name: str) -> str:
    """Save a matplotlib figure with standard settings."""
    if output_path is None:
        output_path = Path(default_name)
    output_path = Path(output_path)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(output_path)


def build_radar_chart(
    dimension_scores: list[DimensionScore],
    baseline_scores: Optional[dict] = None,
    output_path: Optional[Path] = None,
) -> str:
    """Generate a radar chart of dimension scores.

    Args:
        dimension_scores: List of dimension scores
        baseline_scores: Optional dict of {dimension_name: score} for comparison
        output_path: Where to save the PNG. If None, auto-generated.

    Returns:
        Path to the saved chart file.
    """
    names = [ds.dimension_name for ds in dimension_scores]
    scores = [ds.raw_score for ds in dimension_scores]
    n = len(names)

    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    scores_plot = scores + [scores[0]]
    angles_plot = angles + [angles[0]]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))

    # Current scores
    ax.plot(angles_plot, scores_plot, "o-", linewidth=2, color="#2196F3", label="Current")
    ax.fill(angles_plot, scores_plot, alpha=0.25, color="#2196F3")

    # Baseline comparison
    if baseline_scores:
        baseline_vals = [baseline_scores.get(name, 0) for name in names]
        baseline_plot = baseline_vals + [baseline_vals[0]]
        ax.plot(angles_plot, baseline_plot, "o--", linewidth=2, color="#FF9800", label="Baseline")
        ax.fill(angles_plot, baseline_plot, alpha=0.1, color="#FF9800")

    ax.set_xticks(angles)
    ax.set_xticklabels(names, fontsize=10)
    ax.set_ylim(0, 100)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(["20", "40", "60", "80", "100"], fontsize=8)
    ax.set_title("Agent Evaluation Radar Chart", fontsize=14, pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1))

    plt.tight_layout()
    return _save_figure(fig, output_path, "radar_chart.png")


def build_trend_chart(
    historical_results: list[CompositeResult],
    output_path: Optional[Path] = None,
) -> str:
    """Generate a trend line chart from historical evaluation results.

    Args:
        historical_results: List of past CompositeResults (oldest first).
        output_path: Where to save the PNG.

    Returns:
        Path to the saved chart file.
    """
    if not historical_results:
        return ""

    # Sort by timestamp
    results = sorted(historical_results, key=lambda r: r.timestamp)
    labels = [r.run_id for r in results]

    # Build dimension data series
    dimension_names = [ds.dimension_name for ds in results[0].dimension_scores]
    dimension_data = {name: [] for name in dimension_names}
    total_scores = []

    for result in results:
        total_scores.append(result.total_score)
        for ds in result.dimension_scores:
            dimension_data[ds.dimension_name].append(ds.raw_score)

    fig, ax = plt.subplots(figsize=(12, 6))

    colors = ["#2196F3", "#4CAF50", "#FF9800", "#9C27B0", "#F44336"]
    markers = ["o", "s", "^", "D", "v"]

    for i, name in enumerate(dimension_names):
        ax.plot(
            labels,
            dimension_data[name],
            marker=markers[i % len(markers)],
            color=colors[i % len(colors)],
            label=name,
            linewidth=1.5,
        )

    # Total score line (thicker)
    ax.plot(
        labels,
        total_scores,
        marker="*",
        color="#333333",
        label="Total Score",
        linewidth=2.5,
        linestyle="--",
    )

    ax.set_xlabel("Evaluation Run", fontsize=11)
    ax.set_ylabel("Score (0-100)", fontsize=11)
    ax.set_title("Agent Evaluation Trend", fontsize=14)
    ax.set_ylim(0, 105)
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(True, alpha=0.3)

    if len(labels) > 5:
        plt.xticks(rotation=45, ha="right")

    plt.tight_layout()
    return _save_figure(fig, output_path, "trend_chart.png")


def build_maturity_chart(
    trajectory: list[MaturityStep],
    output_path: Optional[Path] = None,
) -> str:
    """Generate a step chart of the L1 -> L4 maturity ladder over runs.

    Args:
        trajectory: Maturity steps (oldest first).
        output_path: Where to save the PNG.

    Returns:
        Path to the saved chart, or "" if trajectory is empty.
    """
    if not trajectory:
        return ""

    labels = [m.run_id for m in trajectory]
    codes = [m.level_code for m in trajectory]
    totals = [m.total_score for m in trajectory]
    x = list(range(len(labels)))

    fig, ax = plt.subplots(figsize=(max(8, len(labels) * 1.2), 5))
    ax.step(x, codes, where="mid", linewidth=2.5, color="#2196F3",
            marker="o", markersize=8, label="Maturity level")

    # Annotate each point with level label + total score.
    for xi, code, total, label in zip(x, codes, totals, labels):
        ax.annotate(
            f"L{code}\n{total:.1f}",
            (xi, code),
            textcoords="offset points",
            xytext=(0, 10),
            ha="center",
            fontsize=8,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_yticks([1, 2, 3, 4])
    ax.set_yticklabels(["L1 - Initial", "L2 - Growing", "L3 - Mature", "L4 - Excellent"], fontsize=9)
    ax.set_ylim(0.5, 4.5)
    ax.set_title("Agent Maturity Trajectory (L1 → L4)", fontsize=14)
    ax.grid(True, axis="y", alpha=0.3)

    if len(labels) > 5:
        plt.xticks(rotation=45, ha="right")

    plt.tight_layout()
    return _save_figure(fig, output_path, "maturity_chart.png")


def build_slope_chart(
    slopes: list[DimensionSlope],
    output_path: Optional[Path] = None,
) -> str:
    """Generate a horizontal bar chart of per-dimension improvement slopes.

    Args:
        slopes: Dimension slopes (any order; sorted desc upstream).
        output_path: Where to save the PNG.

    Returns:
        Path to the saved chart, or "" if slopes is empty.
    """
    if not slopes:
        return ""

    names = [s.dimension_name for s in slopes]
    vals = [s.slope for s in slopes]
    colors = [
        "#4CAF50" if s.trend == "improving"
        else "#F44336" if s.trend == "declining"
        else "#9E9E9E"
        for s in slopes
    ]

    fig, ax = plt.subplots(figsize=(10, max(4, len(names) * 0.6)))
    y = list(range(len(names)))
    ax.barh(y, vals, color=colors)

    for yi, val in zip(y, vals):
        ax.text(val, yi, f" {val:+.2f}", va="center", fontsize=9,
                color="#333")

    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=10)
    ax.axvline(0, color="#333", linewidth=0.8)
    ax.set_xlabel("Improvement rate (points / run)", fontsize=11)
    ax.set_title("Per-Dimension Improvement Slope", fontsize=14)
    ax.invert_yaxis()  # top improver on top

    plt.tight_layout()
    return _save_figure(fig, output_path, "slope_chart.png")
