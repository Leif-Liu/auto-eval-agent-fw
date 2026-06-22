"""CLI entry point for the evaluation framework."""

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config import (
    RAGFLOW_API_KEY, RAGFLOW_BASE_URL, RAGFLOW_AGENT_ID, AGENT_TIMEOUT_SEC,
    OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL,
    TEST_DATA_DIR, RESULTS_DIR,
)

console = Console()


@click.group()
def cli():
    """Master Agent Evaluation Framework."""
    pass


def _create_agent_client():
    """Create and connect the RAGFlow agent client."""
    from src.agent_client import AgentClient
    client = AgentClient(
        api_key=RAGFLOW_API_KEY,
        base_url=RAGFLOW_BASE_URL,
        agent_id=RAGFLOW_AGENT_ID,
        timeout=AGENT_TIMEOUT_SEC,
    )
    return client


def _create_llm_judge():
    """Create the LLM judge with vLLM endpoint."""
    from src.llm_judge import LLMJudge
    return LLMJudge(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        model=OPENAI_MODEL,
    )


@cli.command()
@click.option("--sample-limit", type=int, default=None, help="Limit number of standard samples to evaluate")
@click.option("--dimensions", "-d", multiple=True, help="Run specific dimensions only (summary_accuracy, conflict_detection, grammar_correction, system_stability, output_quality)")
@click.option("--output-dir", "-o", type=click.Path(), default=None, help="Override output directory")
@click.option("--baseline", type=click.Path(exists=True), default=None, help="Path to baseline report JSON for comparison")
@click.option("--no-charts", is_flag=True, help="Skip chart generation")
def run(sample_limit, dimensions, output_dir, baseline, no_charts):
    """Run full evaluation pipeline."""
    if not RAGFLOW_API_KEY or not RAGFLOW_AGENT_ID:
        console.print("[red]Error: RAGFLOW_API_KEY and RAGFLOW_AGENT_ID must be set in .env[/red]")
        sys.exit(1)
    if not OPENAI_API_KEY:
        console.print("[red]Error: OPENAI_API_KEY not set. Set it in .env or environment variable.[/red]")
        sys.exit(1)

    console.print(Panel("Starting Evaluation Pipeline", style="bold blue"))

    agent_client = _create_agent_client()
    llm_judge = _create_llm_judge()

    # Connect to agent
    console.print("Connecting to RAGFlow agent... ", end="")
    if not agent_client.connect():
        console.print(f"[yellow]Warning: Could not connect to agent {RAGFLOW_AGENT_ID}[/yellow]")
        console.print("Continuing anyway (agent calls will fail gracefully)...")
    else:
        console.print("[green]OK[/green]")

    results_dir = Path(output_dir) if output_dir else RESULTS_DIR
    dim_list = list(dimensions) if dimensions else None

    orchestrator = _create_orchestrator(agent_client, llm_judge, results_dir)

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
    if not RAGFLOW_API_KEY or not RAGFLOW_AGENT_ID:
        console.print("[red]Error: RAGFLOW_API_KEY and RAGFLOW_AGENT_ID must be set.[/red]")
        sys.exit(1)

    console.print(Panel(f"Quick Run — {sample_limit} samples", style="bold yellow"))

    agent_client = _create_agent_client()
    llm_judge = _create_llm_judge()

    if not agent_client.connect():
        console.print(f"[yellow]Warning: Could not connect to agent {RAGFLOW_AGENT_ID}[/yellow]")

    orchestrator = _create_orchestrator(agent_client, llm_judge)

    output = orchestrator.run_full_evaluation(sample_limit=sample_limit)
    console.print(output["summary"])
    console.print(f"\n[dim]Results: {output['run_dir']}[/dim]")


def _create_orchestrator(agent_client, llm_judge, results_dir=None):
    from src.orchestrator import EvaluationOrchestrator
    return EvaluationOrchestrator(
        agent_client=agent_client,
        llm_judge=llm_judge,
        test_data_dir=Path(TEST_DATA_DIR),
        results_dir=results_dir or RESULTS_DIR,
    )


@cli.command()
def check():
    """Verify environment: RAGFlow connectivity, LLM judge, test data integrity."""
    from src.data.loader import load_test_data, get_dataset_stats

    issues = []

    # Check RAGFlow config
    if RAGFLOW_API_KEY and RAGFLOW_AGENT_ID:
        console.print(f"[green]✓[/green] RAGFLOW config set (agent: {RAGFLOW_AGENT_ID})")
    else:
        console.print("[red]✗[/red] RAGFLOW_API_KEY or RAGFLOW_AGENT_ID not set")
        issues.append("RAGFLOW_CONFIG")

    # Check LLM judge config
    if OPENAI_API_KEY:
        console.print(f"[green]✓[/green] LLM Judge config set (model: {OPENAI_MODEL}, endpoint: {OPENAI_BASE_URL})")
    else:
        console.print("[red]✗[/red] OPENAI_API_KEY not set")
        issues.append("OPENAI_API_KEY")

    # Check RAGFlow connectivity
    from src.agent_client import AgentClient
    agent = AgentClient(
        api_key=RAGFLOW_API_KEY,
        base_url=RAGFLOW_BASE_URL,
        agent_id=RAGFLOW_AGENT_ID,
    )
    if agent.health_check():
        console.print(f"[green]✓[/green] RAGFlow agent reachable at {RAGFLOW_BASE_URL}")
    else:
        console.print(f"[yellow]✗[/yellow] RAGFlow agent not reachable")
        issues.append("RAGFLOW_AGENT")

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
@click.option("--results-dir", "results_dir", type=click.Path(), default=None,
              help="Results directory to read (default: RESULTS_DIR).")
@click.option("--limit", type=int, default=50,
              help="Max number of most-recent runs to analyze (slopes/deltas computed over this window).")
@click.option("--charts", is_flag=True, help="Also render maturity + slope PNG charts under results/monitoring/.")
@click.option("--no-insight", is_flag=True, help="Skip the LLM evolution-narrative analysis (offline mode).")
def dashboard(results_dir, limit, charts, no_insight):
    """Quantify the evaluated agent's capability growth across iterations.

    Aggregates archived runs under results/ into run-over-run deltas, the
    L1->L4 maturity trajectory, and per-dimension improvement slopes. Optionally
    renders PNG charts and an LLM-generated Chinese evolution narrative.

    Does NOT require RAGFlow; the insight narrative needs vLLM (OPENAI_*).
    """
    from src.monitoring.dashboard import render_dashboard

    rd = Path(results_dir) if results_dir else RESULTS_DIR

    llm_judge = None
    if not no_insight:
        if not OPENAI_API_KEY:
            console.print("[yellow]OPENAI_API_KEY not set — insight narrative disabled.[/yellow]")
        else:
            llm_judge = _create_llm_judge()

    render_dashboard(
        console=console,
        results_dir=rd,
        limit=limit,
        charts=charts,
        insight=(not no_insight),
        llm_judge=llm_judge,
    )


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


@cli.group()
def dataset():
    """Test set expansion / data flywheel tooling (import, stats, dedup-check)."""
    pass


@dataset.command("import")
@click.argument("input_path", type=click.Path(exists=True))
@click.option("--target", "target", type=click.Path(), default=None,
              help="Target standard_samples.json (default: TEST_DATA_DIR/standard/standard_samples.json).")
@click.option("--version", default=None, help="Version tag for this batch (default: DATASET_DEFAULT_VERSION).")
@click.option("--no-llm", is_flag=True, help="Skip LLM drafting; pure interactive annotation.")
@click.option("--dry-run", is_flag=True, help="Draft/validate/dedup only; do not write.")
@click.option("--auto-accept", is_flag=True,
              help="Accept LLM drafts without prompts (batch mode; cases with no draft are skipped).")
def dataset_import(input_path, target, version, no_llm, dry_run, auto_accept):
    """Annotate raw production cases and append them to the golden test set.

    Does NOT require RAGFlow. Uses the vLLM LLM judge to draft candidate
    annotations which an expert refines; degrades to pure interactive
    annotation when vLLM is unreachable or --no-llm is set.
    """
    from pathlib import Path

    from config import (
        DATASET_BACKUP_DIR,
        DATASET_CHANGELOG_PATH,
        DATASET_DEFAULT_VERSION,
        DATASET_DEDUP_THRESHOLD,
    )
    from src.dataset_tool.pipeline import run_import_workflow

    target_file = Path(target) if target else TEST_DATA_DIR / "standard" / "standard_samples.json"
    ver = version or DATASET_DEFAULT_VERSION

    if not target_file.exists():
        console.print(f"[red]Target file not found: {target_file}[/red]")
        sys.exit(1)

    use_llm = (not no_llm) and bool(OPENAI_API_KEY)
    llm_judge = None
    if use_llm:
        llm_judge = _create_llm_judge()
        console.print(f"[green]✓[/green] LLM drafting enabled (model: {OPENAI_MODEL})")
    else:
        console.print("[yellow]LLM drafting disabled — pure interactive annotation[/yellow]")

    console.print(Panel(f"Flywheel import — {input_path}", style="bold blue"))
    summary = run_import_workflow(
        input_path=Path(input_path),
        console=console,
        target_file=target_file,
        version=ver,
        use_llm=use_llm,
        dry_run=dry_run,
        auto_accept=auto_accept,
        llm_judge=llm_judge,
        dedup_threshold=DATASET_DEDUP_THRESHOLD,
        backup_dir=DATASET_BACKUP_DIR,
        changelog_path=DATASET_CHANGELOG_PATH,
    )

    table = Table(title="Import Summary")
    table.add_column("metric", style="cyan")
    table.add_column("value", style="green", justify="right")
    for field in ("total_raw", "drafted", "refined", "rejected",
                  "duplicates_skipped", "validation_errors", "added"):
        table.add_row(field, str(getattr(summary, field)))
    console.print(table)
    console.print(
        f"[dim]version={summary.version} target={summary.target_file} "
        f"backup={summary.backup_file} degraded={summary.degraded}[/dim]"
    )


@dataset.command("stats")
@click.option("--target", "target", type=click.Path(exists=True), default=None,
              help="Standard samples file (default: TEST_DATA_DIR/standard/standard_samples.json).")
def dataset_stats(target):
    """Show distribution stats and balance hints for the standard test set."""
    from pathlib import Path

    from src.data.loader import load_standard_samples
    from src.dataset_tool.stats import describe_distribution, suggest_balance_action

    target_file = Path(target) if target else TEST_DATA_DIR / "standard" / "standard_samples.json"
    samples = load_standard_samples(target_file)
    report = describe_distribution(samples)

    def _rows(d, label):
        return [(f"  {label}: {k}", str(v)) for k, v in sorted(d.items())]

    table = Table(title=f"Dataset Distribution (n={report.total})")
    table.add_column("bucket", style="cyan")
    table.add_column("count", style="green", justify="right")
    for k, v in sorted(report.by_difficulty.items()):
        table.add_row(f"difficulty: {k}", str(v))
    for r in _rows(report.by_metadata_source, "source"):
        table.add_row(*r)
    for r in _rows(report.summary_length_buckets, "summary_len"):
        table.add_row(*r)
    for r in _rows(report.conflict_density, "conflicts"):
        table.add_row(*r)
    for r in _rows(report.grammar_density, "grammar_errors"):
        table.add_row(*r)
    table.add_row("structured_header", str(report.field_coverage.get("structured_header", 0)))
    console.print(table)

    hints = suggest_balance_action(report)
    if hints:
        console.print("[yellow]Balance hints:[/yellow]")
        for h in hints:
            console.print(f"  • {h}")
    else:
        console.print("[green]Difficulty distribution within target range.[/green]")


@dataset.command("dedup-check")
@click.argument("candidates_file", type=click.Path(exists=True))
@click.option("--target", "target", type=click.Path(exists=True), default=None,
              help="Existing standard samples to compare against (default: the golden set).")
@click.option("--threshold", type=float, default=None, help="Similarity threshold (default: DATASET_DEDUP_THRESHOLD).")
def dataset_dedup_check(candidates_file, target, threshold):
    """Check a candidate samples file for near-duplicates against the golden set."""
    from pathlib import Path

    from config import DATASET_DEDUP_THRESHOLD
    from src.data.loader import load_standard_samples
    from src.data.writer import find_duplicates

    candidates = load_standard_samples(Path(candidates_file))
    existing_path = Path(target) if target else TEST_DATA_DIR / "standard" / "standard_samples.json"
    existing = load_standard_samples(existing_path) if existing_path.exists() else []
    thr = threshold if threshold is not None else DATASET_DEDUP_THRESHOLD
    hits = find_duplicates(candidates, existing, threshold=thr)

    if not hits:
        console.print(f"[green]No duplicates above threshold {thr} "
                      f"({len(candidates)} candidates vs {len(existing)} existing).[/green]")
        return

    table = Table(title=f"Duplicate Check (threshold={thr})")
    table.add_column("candidate", style="cyan")
    table.add_column("↔ existing", style="magenta")
    table.add_column("similarity", justify="right")
    table.add_column("verdict")
    for h in hits:
        cand_id = candidates[h.candidate_idx].sample_id
        table.add_row(cand_id, h.existing_sample_id, str(h.similarity), h.verdict)
    console.print(table)


if __name__ == "__main__":
    cli()
