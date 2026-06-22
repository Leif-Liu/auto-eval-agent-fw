"""Evolution dashboard render + orchestration layer.

Reads archived ``results/*/report.json`` runs, computes deterministic evolution
metrics (via :mod:`src.monitoring.metrics`), renders them as rich tables, and
optionally generates PNG charts and an LLM-generated Chinese evolution
narrative. Read-only and offline-capable; only the insight narrative needs vLLM.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from config import (
    MONITORING_REGRESSION_THRESHOLD,
    MONITORING_SLOPE_MIN_RUNS,
    RESULTS_DIR,
)
from src.models.evaluation_result import CompositeResult
from src.models.monitoring import DimensionSlope, EvolutionDigest, MaturityStep, RunDelta
from src.monitoring.metrics import (
    build_evolution_digest,
    compute_dimension_slopes,
    compute_maturity_trajectory,
    compute_run_deltas,
)
from src.reporting.chart_builder import build_maturity_chart, build_slope_chart
from src.reporting.report_generator import load_previous_results

if TYPE_CHECKING:
    from src.llm_judge import LLMJudge

logger = logging.getLogger(__name__)

# Maturity ladder code -> rich color.
_LEVEL_COLORS = {1: "red", 2: "yellow", 3: "cyan", 4: "green"}


def render_dashboard(
    console: Console,
    results_dir: Path = RESULTS_DIR,
    limit: int = 50,
    charts: bool = False,
    insight: bool = True,
    llm_judge: "Optional[LLMJudge]" = None,
) -> dict:
    """Render the evolution dashboard to ``console``.

    Returns a dict of computed artifacts (for programmatic / test use):
    ``n_runs``, ``chart_paths``, ``deltas``, ``trajectory``, ``slopes``.
    """
    history = load_previous_results(Path(results_dir), limit=limit)
    artifacts: dict = {"n_runs": len(history), "chart_paths": []}

    if not history:
        console.print(Panel(
            "[yellow]No evaluation results found.[/yellow]\n"
            f"Run [cyan]eval-framework run[/cyan] first to populate {results_dir}.",
            title="Agent Evolution Dashboard",
        ))
        return artifacts

    deltas = compute_run_deltas(history)
    trajectory = compute_maturity_trajectory(history)
    slopes = compute_dimension_slopes(history, min_runs=MONITORING_SLOPE_MIN_RUNS)

    console.print(Panel(
        _headline(trajectory),
        title="Agent Evolution Dashboard",
        style="bold blue",
    ))

    _render_delta_table(console, deltas)
    _render_maturity_table(console, trajectory)
    _render_slope_table(console, slopes)

    if charts:
        _render_charts(console, Path(results_dir), trajectory, slopes, artifacts)

    if insight:
        _maybe_render_insight(console, history, slopes, llm_judge)

    artifacts.update({"deltas": deltas, "trajectory": trajectory, "slopes": slopes})
    return artifacts


def _headline(trajectory: list[MaturityStep]) -> str:
    first, last = trajectory[0], trajectory[-1]
    total_delta = last.total_score - first.total_score
    if total_delta > 0:
        arrow = "[green]↑[/green]"
    elif total_delta < 0:
        arrow = "[red]↓[/red]"
    else:
        arrow = "[dim]→[/dim]"
    return (
        f"Maturity ladder: {first.level_label} → {last.level_label}    "
        f"Runs: [bold]{len(trajectory)}[/bold]\n"
        f"Total score: [bold]{first.total_score:.1f}[/bold] → "
        f"[bold]{last.total_score:.1f}[/bold] ({arrow} {total_delta:+.1f})    "
        f"Span: [cyan]{first.run_id}[/cyan] → [cyan]{last.run_id}[/cyan]"
    )


def _render_delta_table(console: Console, deltas: list[RunDelta]) -> None:
    table = Table(title="Run-over-run Score Delta (newest first)")
    table.add_column("run_id", style="cyan", no_wrap=True)
    table.add_column("total", justify="right")
    table.add_column("Δ vs prev", justify="right")
    table.add_column("maturity")

    for d in reversed(deltas):  # newest first
        table.add_row(
            d.run_id,
            f"{d.total_score:.1f}",
            _format_delta(d.total_delta),
            d.maturity_level,
        )
    console.print(table)


def _format_delta(delta: Optional[float]) -> str:
    if delta is None:
        return "[dim]—[/dim]"
    if delta <= MONITORING_REGRESSION_THRESHOLD:
        return f"[red]{delta:+.1f} ▼[/red]"
    if delta < 0:
        return f"[yellow]{delta:+.1f}[/yellow]"
    if delta == 0:
        return "[dim]±0.0[/dim]"
    return f"[green]{delta:+.1f} ▲[/green]"


def _ladder(code: int) -> str:
    color = _LEVEL_COLORS.get(code, "white")
    filled = "█" * max(code, 0)
    empty = "░" * max(4 - code, 0)
    return f"[{color}]{filled}[/][dim]{empty}[/]"


def _render_maturity_table(console: Console, trajectory: list[MaturityStep]) -> None:
    table = Table(title="Maturity Trajectory — L1 → L4 (newest first)")
    table.add_column("run_id", style="cyan", no_wrap=True)
    table.add_column("level", justify="center")
    table.add_column("ladder", justify="left")
    table.add_column("total", justify="right")

    for m in reversed(trajectory):  # newest first
        table.add_row(m.run_id, m.level_label, _ladder(m.level_code), f"{m.total_score:.1f}")
    console.print(table)


def _render_slope_table(console: Console, slopes: list[DimensionSlope]) -> None:
    if not slopes:
        console.print(Panel(
            "[yellow]Per-dimension slope not available:[/yellow] need at least "
            f"[cyan]{MONITORING_SLOPE_MIN_RUNS}[/cyan] runs sharing a dimension set. "
            "Run more evaluations to enable improvement-rate analysis.",
            title="Per-Dimension Improvement Slope",
        ))
        return

    table = Table(title="Per-Dimension Improvement Slope (points / run)")
    table.add_column("dimension", style="cyan")
    table.add_column("slope", justify="right")
    table.add_column("R²", justify="right")
    table.add_column("first → last", justify="right")
    table.add_column("trend")

    for s in slopes:
        slope_str = f"[green]{s.slope:+.2f}[/green]" if s.slope >= 0 else f"[red]{s.slope:+.2f}[/red]"
        if s.trend == "improving":
            trend_str = "[green]▲ improving[/green]"
        elif s.trend == "declining":
            trend_str = "[red]▼ declining[/red]"
        else:
            trend_str = "[dim]→ flat[/dim]"
        table.add_row(
            s.dimension_name,
            slope_str,
            f"{s.r_squared:.2f}",
            f"{s.first_score:.1f} → {s.last_score:.1f}",
            trend_str,
        )
    console.print(table)


def _render_charts(
    console: Console,
    results_dir: Path,
    trajectory: list[MaturityStep],
    slopes: list[DimensionSlope],
    artifacts: dict,
) -> None:
    out_dir = results_dir / "monitoring"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        path = build_maturity_chart(trajectory, output_path=out_dir / "maturity_chart.png")
        if path:
            artifacts["chart_paths"].append(path)
            console.print(f"[green]Maturity chart:[/green] {path}")
    except Exception as e:
        logger.warning(f"Maturity chart generation failed: {e}")
        console.print(f"[yellow]Maturity chart skipped: {e}[/yellow]")

    if not slopes:
        return
    try:
        path = build_slope_chart(slopes, output_path=out_dir / "slope_chart.png")
        if path:
            artifacts["chart_paths"].append(path)
            console.print(f"[green]Slope chart:[/green] {path}")
    except Exception as e:
        logger.warning(f"Slope chart generation failed: {e}")
        console.print(f"[yellow]Slope chart skipped: {e}[/yellow]")


def _build_digest_text(digest: EvolutionDigest) -> str:
    """Compact plain-text digest fed into the insight prompt."""
    lines = [
        f"runs: {digest.n_runs}",
        f"span: {digest.span_first_run_id} -> {digest.span_last_run_id}",
        f"total_score: {digest.first_total} -> {digest.last_total} "
        f"(delta {digest.total_delta:+.1f})",
        f"maturity: {digest.maturity_first} -> {digest.maturity_last}",
    ]
    if digest.dimension_slopes:
        lines.append("per-dimension slope (points/run):")
        for s in digest.dimension_slopes:
            lines.append(
                f"  - {s.dimension_name}: slope={s.slope:+.2f} (R^2={s.r_squared:.2f}) "
                f"{s.first_score}->{s.last_score} {s.trend}"
            )
    if digest.top_improver:
        lines.append(f"top_improver: {digest.top_improver}")
    if digest.top_regressor:
        lines.append(f"top_regressor: {digest.top_regressor}")
    if digest.largest_run_delta:
        lines.append(
            f"largest single-run total delta: run {digest.largest_run_delta['run_id']} "
            f"{digest.largest_run_delta['delta']:+.1f}"
        )
    return "\n".join(lines)


def _maybe_render_insight(
    console: Console,
    history: list[CompositeResult],
    slopes: list[DimensionSlope],
    llm_judge: "Optional[LLMJudge]",
) -> None:
    if llm_judge is None:
        return

    digest = build_evolution_digest(history, slopes)
    digest_text = _build_digest_text(digest)

    with console.status("[dim]Generating evolution narrative via vLLM (temperature=0)...[/dim]"):
        try:
            narrative = llm_judge.judge_evolution(digest_text)
        except Exception as e:
            logger.warning(f"Evolution insight failed: {e}")
            narrative = ""

    if not narrative:
        console.print(
            "[yellow]Insight narrative unavailable (vLLM unreachable or empty response). "
            "The quantitative tables above remain valid.[/yellow]"
        )
        return

    console.print(Panel(
        Markdown(narrative),
        title="Evolution Insight (LLM-as-analyst)",
        border_style="magenta",
    ))
