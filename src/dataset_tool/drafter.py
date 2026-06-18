"""LLM-assisted drafting of a StandardSample candidate from a raw case.

Reuses the existing :class:`~src.llm_judge.LLMJudge` (vLLM, temperature=0.0,
three-level JSON fallback). Any failure — connection error, unparseable
response, or a draft that does not fit the schema — degrades gracefully so the
caller can fall back to manual annotation.
"""

import logging

from src.llm_judge import LLMJudge
from src.models.dataset_draft import DraftResult, RawProductionCase, StandardSampleDraft
from src.models.test_data import (
    ConflictAnnotation,
    DifficultyLevel,
    GrammarErrorAnnotation,
)

logger = logging.getLogger(__name__)


def draft_standard_sample(
    llm_judge: LLMJudge, raw: RawProductionCase, retries: int = 2
) -> DraftResult:
    """Ask the LLM judge to draft a candidate sample for one raw case.

    Always returns a :class:`DraftResult` (never raises). ``success=False``
    carries a ``degraded_reason`` the CLI can surface to the user.
    """
    try:
        result = llm_judge.judge_draft_standard_sample(
            input_description=raw.input_description,
            agent_response=raw.agent_response,
            retries=retries,
        )
    except Exception as e:  # connection / endpoint down
        logger.warning(f"LLM draft call failed: {e}")
        return DraftResult(success=False, degraded_reason=f"LLM call failed: {e}")

    # On parse failure _call_with_retry returns the summary-shaped error dict,
    # which has no ground_truth_summary key.
    if "ground_truth_summary" not in result:
        reason = result.get("reasoning", "missing ground_truth_summary in LLM response")
        logger.warning(f"LLM draft degraded: {reason}")
        return DraftResult(
            success=False,
            degraded_reason=f"LLM returned no usable draft: {reason}",
            raw_llm_response=result,
        )

    try:
        draft = StandardSampleDraft(
            input_description=raw.input_description,
            ground_truth_summary=result.get("ground_truth_summary", ""),
            conflict_annotations=[
                ConflictAnnotation(**c) for c in result.get("conflict_annotations", [])
            ],
            grammar_error_annotations=[
                GrammarErrorAnnotation(**g)
                for g in result.get("grammar_error_annotations", [])
            ],
            difficulty=_coerce_difficulty(result.get("difficulty", "medium")),
            draft_confidence=float(result.get("draft_confidence", 0.0) or 0.0),
            draft_notes=result.get("draft_notes", ""),
        )
    except Exception as e:
        logger.warning(f"LLM draft shape invalid: {e}")
        return DraftResult(
            success=False,
            degraded_reason=f"invalid draft shape: {e}",
            raw_llm_response=result,
        )

    return DraftResult(success=True, draft=draft, raw_llm_response=result)


def _coerce_difficulty(value) -> DifficultyLevel:
    """Best-effort coerce an LLM-provided difficulty into the enum."""
    try:
        return DifficultyLevel(str(value).strip().lower())
    except ValueError:
        return DifficultyLevel.MEDIUM
