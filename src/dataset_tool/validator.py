"""Validation for standard samples (schema-level + business rules)."""

import re

from src.models.dataset_draft import ValidationIssue
from src.models.test_data import DifficultyLevel, StandardSample

# Convention used by the existing golden set (STD-001 .. STD-005).
SAMPLE_ID_RE = re.compile(r"^STD-\d{3,}$")


def validate_samples(
    samples: list[StandardSample],
    existing: list[StandardSample] | None = None,
) -> list[ValidationIssue]:
    """Validate a batch of samples.

    Checks: ``sample_id`` presence / ``STD-NNN`` convention / uniqueness against
    ``existing`` and within the batch; non-empty ``input_description`` and
    ``ground_truth_summary``; valid ``difficulty``. Returns a list of
    :class:`ValidationIssue` (empty means clean).
    """
    existing = existing or []
    existing_ids = {s.sample_id for s in existing}
    seen: set[str] = set()
    issues: list[ValidationIssue] = []

    for s in samples:
        sid = s.sample_id

        if not sid:
            issues.append(
                ValidationIssue("<none>", "error", "sample_id", "missing sample_id")
            )
        else:
            if not SAMPLE_ID_RE.match(sid):
                issues.append(
                    ValidationIssue(
                        sid,
                        "warning",
                        "sample_id",
                        f"id '{sid}' does not match STD-NNN convention",
                    )
                )
            if sid in existing_ids:
                issues.append(
                    ValidationIssue(
                        sid, "error", "sample_id", f"id '{sid}' already exists in dataset"
                    )
                )
            if sid in seen:
                issues.append(
                    ValidationIssue(
                        sid, "error", "sample_id", f"duplicate id '{sid}' within batch"
                    )
                )
            seen.add(sid)

        if not (s.input_description or "").strip():
            issues.append(
                ValidationIssue(sid or "<none>", "error", "input_description", "empty")
            )
        if not (s.ground_truth_summary or "").strip():
            issues.append(
                ValidationIssue(sid or "<none>", "error", "ground_truth_summary", "empty")
            )
        try:
            DifficultyLevel(s.difficulty)
        except ValueError:
            issues.append(
                ValidationIssue(
                    sid or "<none>",
                    "error",
                    "difficulty",
                    f"invalid difficulty '{s.difficulty}'",
                )
            )

    return issues
