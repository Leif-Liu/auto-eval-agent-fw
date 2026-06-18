"""End-to-end import workflow for the data flywheel.

load raw cases → (optional) LLM draft → expert refine → dedup → validate →
backup + version-tagged write + changelog. Returns an :class:`ImportSummary`.

The write step reuses :mod:`src.data.writer`; re-evaluation against a baseline
is intentionally NOT part of this module — it stays in the existing
``run_full_evaluation(baseline_path=...)`` API and is triggered via
``eval-framework run --baseline``.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from rich.console import Console

from src.data.loader import load_standard_samples
from src.data.writer import (
    append_changelog,
    backup_standard_file,
    find_duplicates,
    merge_samples,
    tag_with_version,
    write_standard_samples,
)
from src.dataset_tool.drafter import draft_standard_sample
from src.dataset_tool.interactive import refine_sample_interactively
from src.dataset_tool.validator import validate_samples
from src.llm_judge import LLMJudge
from src.models.dataset_draft import ImportSummary, RawProductionBatch, RawProductionCase
from src.models.test_data import StandardSample

logger = logging.getLogger(__name__)

_STD_ID_RE = re.compile(r"STD-(\d+)")


def load_raw_batch(path: Path) -> RawProductionBatch:
    """Load raw cases from JSON.

    Accepts either ``{"source": "...", "cases": [...]}`` or a bare ``[...]``.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return RawProductionBatch(cases=[RawProductionCase(**c) for c in data])
    return RawProductionBatch(**data)


def _next_suggested_ids(existing: list[StandardSample], count: int) -> list[str]:
    """Generate ``STD-NNN`` ids continuing after the max existing numeric suffix."""
    max_n = 0
    for s in existing:
        m = _STD_ID_RE.match(s.sample_id or "")
        if m:
            max_n = max(max_n, int(m.group(1)))
    return [f"STD-{max_n + i + 1:03d}" for i in range(count)]


def run_import_workflow(
    input_path: Path,
    console: Console,
    target_file: Path,
    version: str,
    use_llm: bool,
    dry_run: bool,
    auto_accept: bool,
    llm_judge: Optional[LLMJudge] = None,
    dedup_threshold: float = 0.85,
    backup_dir: Optional[Path] = None,
    changelog_path: Optional[Path] = None,
) -> ImportSummary:
    """Run the full import workflow and return a summary. Never writes when dry_run."""
    batch = load_raw_batch(input_path)
    target_file = Path(target_file)
    existing = (
        load_standard_samples(target_file) if target_file.exists() else []
    )
    suggested_ids = _next_suggested_ids(existing, len(batch.cases))

    refined: list[StandardSample] = []
    drafted = 0
    rejected = 0
    degraded_any = False

    for idx, raw in enumerate(batch.cases):
        draft = None
        if use_llm and llm_judge is not None:
            result = draft_standard_sample(llm_judge, raw)
            if result.success and result.draft is not None:
                drafted += 1
                draft = result.draft
            else:
                degraded_any = True
                console.print(
                    f"[yellow]draft degraded: {result.degraded_reason}[/yellow]"
                )

        sample = refine_sample_interactively(
            raw, draft, console, suggested_ids[idx], auto_accept=auto_accept
        )
        if sample is None:
            rejected += 1
            continue
        refined.append(sample)

    # Dedup against the existing golden set.
    dup_hits = find_duplicates(refined, existing, threshold=dedup_threshold)
    dup_indices: set[int] = set()
    for h in dup_hits:
        if h.verdict == "duplicate":
            dup_indices.add(h.candidate_idx)
            console.print(
                f"[yellow]skip duplicate: candidate ↔ {h.existing_sample_id} "
                f"(sim={h.similarity})[/yellow]"
            )
        else:
            console.print(
                f"[dim]near-dup warning: candidate ↔ {h.existing_sample_id} "
                f"(sim={h.similarity}) — keeping[/dim]"
            )
    deduped = [s for i, s in enumerate(refined) if i not in dup_indices]
    duplicates_skipped = len(refined) - len(deduped)

    # Validate; drop only error-severity issues, keep warnings.
    issues = validate_samples(deduped, existing=existing)
    error_ids = {i.sample_id for i in issues if i.severity == "error"}
    for i in issues:
        color = "red" if i.severity == "error" else "yellow"
        console.print(
            f"[{color}]{i.severity} {i.sample_id} {i.field}: {i.message}[/{color}]"
        )
    clean = [s for s in deduped if s.sample_id not in error_ids]
    validation_errors = len(deduped) - len(clean)

    backup_path: Optional[Path] = None
    added = 0
    if dry_run:
        console.print("[dim]dry-run: no files written[/dim]")
    elif clean:
        if backup_dir is not None:
            backup_path = backup_standard_file(target_file, backup_dir)
        tagged = [tag_with_version(s, version) for s in clean]
        merged = merge_samples(existing, tagged)
        write_standard_samples(merged, target_file)
        if changelog_path is not None:
            append_changelog(
                changelog_path,
                version,
                len(clean),
                note=f"flywheel import ({Path(input_path).name})",
                backup_file=backup_path,
            )
        added = len(clean)
        console.print(f"[green]wrote {added} samples to {target_file}[/green]")

    return ImportSummary(
        input_path=str(input_path),
        total_raw=len(batch.cases),
        drafted=drafted,
        refined=len(refined),
        rejected=rejected,
        duplicates_skipped=duplicates_skipped,
        validation_errors=validation_errors,
        added=added,
        version=version,
        target_file=str(target_file),
        backup_file=str(backup_path) if backup_path else None,
        degraded=degraded_any,
    )
