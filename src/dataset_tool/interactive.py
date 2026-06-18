"""Interactive refinement of an LLM-drafted (or blank) sample by an expert.

The flow for each raw case:
  1. show the raw input + agent output + LLM draft (if any)
  2. expert chooses [a]ccept / [e]dit / [s]kip
  3. on edit: multi-line GT via ``$EDITOR``, difficulty via prompt, and
     conflict/grammar annotations edited as JSON in ``$EDITOR``

``auto_accept=True`` skips the prompts and accepts the draft as-is (skipping
cases that have no draft). This is a real batch mode for LLM-only import where
the expert reviews the written file afterward via git diff — and it also makes
the pipeline smoke-testable without a TTY.
"""

import json

import click
from rich.console import Console
from rich.panel import Panel

from src.models.dataset_draft import RawProductionCase, StandardSampleDraft
from src.models.test_data import (
    ConflictAnnotation,
    DifficultyLevel,
    GrammarErrorAnnotation,
    StandardSample,
)

_AGENT_OUTPUT_PREVIEW = 400


def refine_sample_interactively(
    raw: RawProductionCase,
    draft: StandardSampleDraft | None,
    console: Console,
    suggested_id: str,
    default_difficulty: DifficultyLevel = DifficultyLevel.MEDIUM,
    auto_accept: bool = False,
) -> StandardSample | None:
    """Refine one raw case into a :class:`StandardSample`.

    Returns ``None`` when the expert discards the case (or, in auto_accept
    mode, when there is no draft to accept).
    """
    _display_case(raw, draft, console)

    if auto_accept:
        if draft is None:
            console.print("[yellow]auto-accept: no draft, skipping[/yellow]")
            return None
        console.print("[green]auto-accept: taking draft as-is[/green]")
        return _build_from_draft(draft, raw, suggested_id)

    if draft is not None:
        choice = click.prompt(
            "[a]ccept draft / [e]dit / [s]kip", default="a", show_default=False
        ).strip().lower()
    else:
        console.print("[dim]no LLM draft available — editing from blank[/dim]")
        choice = "e"

    if choice == "s":
        return None

    if choice == "a" and draft is not None:
        return _build_from_draft(draft, raw, suggested_id)

    # choice == "e" (or accept with no draft) → field-by-field edit
    gt = draft.ground_truth_summary if draft else ""
    difficulty = draft.difficulty if draft else default_difficulty
    conflicts = list(draft.conflict_annotations) if draft else []
    grammar = list(draft.grammar_error_annotations) if draft else []

    gt = _edit_multiline("Ground Truth Summary", gt, required=True)
    difficulty = click.prompt(
        "Difficulty [easy/medium/complex]",
        default=str(difficulty),
        type=DifficultyLevel,
    )
    conflicts = _edit_annotation_list("Conflict annotations", conflicts, ConflictAnnotation)
    grammar = _edit_annotation_list(
        "Grammar error annotations", grammar, GrammarErrorAnnotation
    )
    sample_id = click.prompt("sample_id", default=suggested_id)

    return StandardSample(
        sample_id=sample_id,
        input_description=raw.input_description,
        ground_truth_summary=gt,
        conflict_annotations=conflicts,
        grammar_error_annotations=grammar,
        difficulty=difficulty,
        metadata=_build_metadata(raw),
    )


def _build_from_draft(
    draft: StandardSampleDraft, raw: RawProductionCase, sample_id: str
) -> StandardSample:
    meta = dict(draft.metadata)
    meta.setdefault("source", "production")
    if raw.case_ref:
        meta.setdefault("case_ref", raw.case_ref)
    sample = draft.to_standard_sample(sample_id)
    return sample.model_copy(update={"metadata": meta})


def _build_metadata(raw: RawProductionCase) -> dict:
    meta: dict = {"source": "production"}
    if raw.case_ref:
        meta["case_ref"] = raw.case_ref
    return meta


def _display_case(
    raw: RawProductionCase, draft: StandardSampleDraft | None, console: Console
) -> None:
    body_lines = [f"[bold]case_ref:[/bold] {raw.case_ref or '(none)'}", ""]
    body_lines.append("[bold]Raw Input Description:[/bold]")
    body_lines.append(raw.input_description)
    if raw.agent_response:
        preview = raw.agent_response
        if len(preview) > _AGENT_OUTPUT_PREVIEW:
            preview = preview[:_AGENT_OUTPUT_PREVIEW] + " …(truncated)"
        body_lines.append("")
        body_lines.append("[bold]Agent Actual Output[/bold] [dim](reference only — NOT ground truth)[/dim]:")
        body_lines.append(preview)
    if draft is not None:
        body_lines.append("")
        body_lines.append(
            f"[bold]LLM Draft[/bold] [dim](confidence={draft.draft_confidence:.2f})[/dim]"
        )
        body_lines.append(f"  GT: {draft.ground_truth_summary}")
        body_lines.append(
            f"  conflicts={len(draft.conflict_annotations)} "
            f"grammar={len(draft.grammar_error_annotations)} "
            f"difficulty={draft.difficulty}"
        )
        if draft.draft_notes:
            body_lines.append(f"  notes: {draft.draft_notes}")
    console.print(Panel("\n".join(body_lines), title="Annotate case", style="cyan"))


def _edit_multiline(label: str, current: str, required: bool) -> str:
    """Edit a (possibly multi-line) text field via ``$EDITOR`` with fallback."""
    initial = current if current else f"# {label} — replace this line\n"
    try:
        edited = click.edit(initial, extension=".txt")
    except Exception:
        edited = None
    if edited is None:
        # editor exited without saving → keep current value
        if required and not current:
            return click.prompt(f"{label} (single line)", default="", show_default=False)
        return current
    edited = _strip_comment_lines(edited).strip()
    if not edited and required:
        return click.prompt(f"{label} (single line)", default="", show_default=False)
    return edited


def _strip_comment_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _edit_annotation_list(label, items, model_cls):
    """Edit a list of annotations as JSON in ``$EDITOR``; keep originals on failure."""
    click.echo(f"{label}: {len(items)} found.")
    if click.confirm("Edit?", default=False):
        raw_json = json.dumps(
            [a.model_dump(mode="json") for a in items], ensure_ascii=False, indent=2
        )
        edited = click.edit(raw_json + "\n", extension=".json")
        if edited:
            try:
                data = json.loads(edited)
                return [model_cls(**d) for d in data]
            except Exception as e:
                click.secho(f"  parse failed ({e}), keeping originals", fg="yellow")
    return items
