"""CLI entry point for the evaluation framework."""

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config import (
    AGENT_BASE_URL, AGENT_TIMEOUT_SEC,
    OPENAI_API_KEY, OPENAI_MODEL,
    TEST_DATA_DIR, RESULTS_DIR,
)

console = Console()


@click.group()
def cli():
    """Defect Description Agent Evaluation Framework."""
    pass


@cli.command()
@click.option("--sample-limit", type=int, default=None, help="Limit number of standard samples to evaluate")
@click.option("--dimensions", "-d", multiple=True, help="Run specific dimensions only (summary_accuracy, conflict_detection, grammar_correction, system_stability, output_quality)")
@click.option("--output-dir", "-o", type=click.Path(), default=None, help="Override output directory")
@click.option("--baseline", type=click.Path(exists=True), default=None, help="Path to baseline report JSON for comparison")
@click.option("--no-charts", is_flag=True, help="Skip chart generation")
def run(sample_limit, dimensions, output_dir, baseline, no_charts):
    """Run full evaluation pipeline."""
    # Validate environment
    if not OPENAI_API_KEY:
        console.print("[red]Error: OPENAI_API_KEY not set. Set it in .env or environment variable.[/red]")
        sys.exit(1)

    from src.agent_client import AgentClient
    from src.llm_judge import LLMJudge
    from src.orchestrator import EvaluationOrchestrator

    console.print(Panel("Starting Evaluation Pipeline", style="bold blue"))

    agent_client = AgentClient(AGENT_BASE_URL, AGENT_TIMEOUT_SEC)
    llm_judge = LLMJudge(OPENAI_API_KEY, OPENAI_MODEL)

    # Check agent connectivity
    console.print("Checking agent connectivity... ", end="")
    if not agent_client.health_check():
        console.print("[yellow]Warning: Agent not reachable at " + AGENT_BASE_URL + "[/yellow]")
        console.print("Continuing anyway (agent calls will fail gracefully)...")
    else:
        console.print("[green]OK[/green]")

    results_dir = Path(output_dir) if output_dir else RESULTS_DIR
    dim_list = list(dimensions) if dimensions else None

    orchestrator = EvaluationOrchestrator(
        agent_client=agent_client,
        llm_judge=llm_judge,
        test_data_dir=Path(TEST_DATA_DIR),
        results_dir=results_dir,
    )

    try:
        with console.status("[bold green]Running evaluation..."):
            output = orchestrator.run_full_evaluation(
                sample_limit=sample_limit,
                dimensions=dim_list,
                baseline_path=Path(baseline) if baseline else None,
            )
    except Exception as e:
        console.print(f"[red]Evaluation failed: {e}[/red]")
        sys.exit(1)

    console.print(output["summary"])
    console.print(f"\n[green]Report saved to: {output['report_path']}[/green]")
    if output.get("radar_chart"):
        console.print(f"[green]Radar chart: {output['radar_chart']}[/green]")
    if output.get("trend_chart"):
        console.print(f"[green]Trend chart: {output['trend_chart']}[/green]")


@cli.command()
@click.option("--sample-limit", type=int, default=5, help="Number of samples for quick run")
def quickrun(sample_limit):
    """Quick evaluation with limited samples for development."""
    from src.agent_client import AgentClient
    from src.llm_judge import LLMJudge
    from src.orchestrator import EvaluationOrchestrator

    if not OPENAI_API_KEY:
        console.print("[red]Error: OPENAI_API_KEY not set.[/red]")
        sys.exit(1)

    console.print(Panel(f"Quick Run — {sample_limit} samples", style="bold yellow"))

    agent_client = AgentClient(AGENT_BASE_URL, AGENT_TIMEOUT_SEC)
    llm_judge = LLMJudge(OPENAI_API_KEY, OPENAI_MODEL)

    orchestrator = EvaluationOrchestrator(
        agent_client=agent_client,
        llm_judge=llm_judge,
    )

    output = orchestrator.run_full_evaluation(sample_limit=sample_limit)
    console.print(output["summary"])
    console.print(f"\n[dim]Results: {output['run_dir']}[/dim]")


@cli.command()
def check():
    """Verify environment: agent connectivity, OpenAI key, test data integrity."""
    from src.data.loader import load_test_data, get_dataset_stats

    issues = []

    # Check OpenAI key
    if OPENAI_API_KEY:
        console.print(f"[green]✓[/green] OPENAI_API_KEY set (model: {OPENAI_MODEL})")
    else:
        console.print("[red]✗[/red] OPENAI_API_KEY not set")
        issues.append("OPENAI_API_KEY")

    # Check agent
    from src.agent_client import AgentClient
    agent = AgentClient(AGENT_BASE_URL, AGENT_TIMEOUT_SEC)
    if agent.health_check():
        console.print(f"[green]✓[/green] Agent reachable at {AGENT_BASE_URL}")
    else:
        console.print(f"[yellow]✗[/yellow] Agent not reachable at {AGENT_BASE_URL}")
        issues.append("AGENT")

    # Check test data
    test_dir = Path(TEST_DATA_DIR)
    if test_dir.exists():
        try:
            dataset = load_test_data(test_dir)
            stats = get_dataset_stats(dataset)
            console.print(f"[green]✓[/green] Test data loaded: {stats['standard_samples']} standard, "
                         f"{stats['anomaly_cases']} anomaly, {stats['incremental_sequences']} incremental")
        except Exception as e:
            console.print(f"[red]✗[/red] Test data error: {e}")
            issues.append("TEST_DATA")
    else:
        console.print(f"[yellow]✗[/yellow] Test data directory not found: {test_dir}")
        issues.append("TEST_DATA")

    if issues:
        console.print(f"\n[yellow]Issues found: {', '.join(issues)}[/yellow]")
    else:
        console.print("\n[green]All checks passed![/green]")


@cli.command()
@click.argument("results_dir", type=click.Path(exists=True), default="results")
def report(results_dir):
    """Regenerate charts and summary from existing evaluation results."""
    from src.reporting.report_generator import load_previous_results, print_summary
    from src.reporting.chart_builder import build_radar_chart, build_trend_chart

    results = load_previous_results(Path(results_dir))
    if not results:
        console.print("[yellow]No evaluation results found.[/yellow]")
        return

    latest = results[0]
    console.print(print_summary(latest))

    if len(results) > 1:
        trend_path = build_trend_chart(results, output_path=Path(results_dir) / latest.run_id / "trend_chart.png")
        console.print(f"\n[green]Trend chart: {trend_path}[/green]")


@cli.command()
def validate():
    """Validate all test data JSON files against schemas."""
    from src.data.loader import load_test_data, get_dataset_stats

    try:
        dataset = load_test_data(Path(TEST_DATA_DIR))
        stats = get_dataset_stats(dataset)

        table = Table(title="Test Dataset Statistics")
        table.add_column("Category", style="cyan")
        table.add_column("Count", style="green", justify="right")

        table.add_row("Standard Samples", str(stats["standard_samples"]))
        table.add_row("Anomaly Cases", str(stats["anomaly_cases"]))
        table.add_row("Incremental Sequences", str(stats["incremental_sequences"]))

        for diff, count in stats.get("difficulty_distribution", {}).items():
            table.add_row(f"  Difficulty: {diff}", str(count))

        for atype, count in stats.get("anomaly_type_distribution", {}).items():
            table.add_row(f"  Anomaly: {atype}", str(count))

        console.print(table)
        console.print("[green]All test data files are valid![/green]")

    except Exception as e:
        console.print(f"[red]Validation failed: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    cli()
