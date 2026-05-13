"""Report generation — JSON output and historical results management."""

import json
import logging
from datetime import datetime
from pathlib import Path

from src.models.evaluation_result import CompositeResult


def write_json_report(result: CompositeResult, output_dir: Path) -> Path:
    """Write full evaluation result to output_dir/report.json.

    Creates directory if needed. Returns path to written file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report_path = output_dir / "report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(), f, indent=2, ensure_ascii=False, default=str)

    return report_path


def load_previous_results(results_dir: Path, limit: int = 10) -> list[CompositeResult]:
    """Load past evaluation results for trend analysis.

    Looks for results/*/report.json files.
    """
    results_dir = Path(results_dir)
    if not results_dir.exists():
        return []

    reports = []
    for run_dir in sorted(results_dir.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        report_file = run_dir / "report.json"
        if not report_file.exists():
            continue
        try:
            with open(report_file, encoding="utf-8") as f:
                data = json.load(f)
            reports.append(CompositeResult(**data))
        except Exception as e:
            logging.getLogger(__name__).warning(f"Could not load report {report_file}: {e}")
            continue
        if len(reports) >= limit:
            break

    return reports


def create_run_dir(results_dir: Path) -> tuple[Path, str]:
    """Create a timestamped results directory and return (path, run_id)."""
    now = datetime.now()
    run_id = now.strftime("%Y-%m-%d_%H%M%S")
    run_dir = Path(results_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir, run_id


def print_summary(result: CompositeResult) -> str:
    """Generate a human-readable summary string."""
    lines = [
        f"{'=' * 60}",
        f"Evaluation Report — {result.run_id}",
        f"Timestamp: {result.timestamp}",
        f"{'=' * 60}",
        "",
        f"Total Score: {result.total_score:.2f} / 100",
        f"Maturity Level: {result.maturity_level}",
        "",
        f"{'Dimension':<25} {'Score':>8} {'Weight':>8} {'Weighted':>10}",
        f"{'-' * 25} {'-' * 8} {'-' * 8} {'-' * 10}",
    ]

    for ds in result.dimension_scores:
        lines.append(
            f"{ds.dimension_name:<25} {ds.raw_score:>8.2f} {ds.weight:>8.0%} {ds.weighted_score:>10.2f}"
        )

    lines.append(f"{'-' * 25} {'-' * 8} {'-' * 8} {'-' * 10}")
    lines.append(f"{'TOTAL':<25} {'':>8} {'':>8} {result.total_score:>10.2f}")

    if result.baseline_comparison:
        lines.append("")
        lines.append("Baseline Comparison:")
        for name, vals in result.baseline_comparison.items():
            if isinstance(vals, dict) and "delta" in vals:
                delta = vals["delta"]
                arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
                lines.append(f"  {name}: {vals['current']:.1f} vs {vals['baseline']:.1f} ({arrow} {abs(delta):.1f})")

    lines.append(f"\n{'=' * 60}")
    return "\n".join(lines)
