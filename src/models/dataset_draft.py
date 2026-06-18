"""Pydantic models for the dataset flywheel tooling.

These models cover the annotation pipeline: raw production cases (no ground
truth), LLM-drafted / in-refine samples, and the reports/summaries produced by
the import workflow. They live alongside the core test-data models and reuse
``StandardSample`` / ``ConflictAnnotation`` / ``GrammarErrorAnnotation`` /
``DifficultyLevel`` from :mod:`src.models.test_data`.
"""

from typing import Optional

from pydantic import BaseModel, Field

from src.models.test_data import (
    ConflictAnnotation,
    DifficultyLevel,
    GrammarErrorAnnotation,
    StandardSample,
)


class RawProductionCase(BaseModel):
    """A raw production case awaiting annotation (no ground truth)."""

    case_ref: str = ""  # production trace id / ticket, for traceability
    input_description: str
    agent_response: str = ""  # the evaluated agent's actual output (reference only)
    extra: dict = Field(default_factory=dict)


class RawProductionBatch(BaseModel):
    """Top-level structure of a flywheel import file."""

    source: str = ""  # origin note (system name / date)
    cases: list[RawProductionCase] = Field(default_factory=list)


class StandardSampleDraft(BaseModel):
    """An LLM-drafted / in-refine sample. Fields are loose to support editing.

    Draft-only fields (``draft_confidence`` / ``draft_notes``) are dropped when
    promoted to a :class:`StandardSample` via :meth:`to_standard_sample`.
    """

    sample_id: Optional[str] = None
    input_description: str
    ground_truth_summary: str = ""
    conflict_annotations: list[ConflictAnnotation] = Field(default_factory=list)
    grammar_error_annotations: list[GrammarErrorAnnotation] = Field(default_factory=list)
    difficulty: DifficultyLevel = DifficultyLevel.MEDIUM
    metadata: dict = Field(default_factory=dict)
    draft_confidence: float = 0.0  # LLM self-rated 0-1
    draft_notes: str = ""  # LLM hint to the human reviewer

    def to_standard_sample(self, sample_id: str) -> StandardSample:
        """Promote this draft into a StandardSample, dropping draft-only fields."""
        return StandardSample(
            sample_id=sample_id,
            input_description=self.input_description,
            ground_truth_summary=self.ground_truth_summary,
            conflict_annotations=self.conflict_annotations,
            grammar_error_annotations=self.grammar_error_annotations,
            difficulty=self.difficulty,
            metadata=self.metadata,
        )


class DraftResult(BaseModel):
    """Outcome of an LLM drafting attempt for one raw case."""

    draft: Optional[StandardSampleDraft] = None
    raw_llm_response: dict = Field(default_factory=dict)
    success: bool = False
    degraded_reason: Optional[str] = None


class DuplicateHit(BaseModel):
    """A near-duplicate detected between a candidate and an existing sample."""

    candidate_idx: int
    existing_sample_id: str
    similarity: float
    verdict: str  # "duplicate" | "near" | "unique"


class ValidationIssue(BaseModel):
    """A single validation problem found on a sample."""

    sample_id: str
    severity: str  # "error" | "warning"
    field: str
    message: str


class DistributionReport(BaseModel):
    """Distribution snapshot of a (current or candidate) standard sample set."""

    total: int
    by_difficulty: dict[str, int]
    by_metadata_source: dict[str, int]
    summary_length_buckets: dict[str, int]  # short / medium / long
    conflict_density: dict[str, int]  # 0 / 1 / 2 / 3+
    grammar_density: dict[str, int]
    field_coverage: dict[str, int]  # Product Line / Security Level coverage


class ImportSummary(BaseModel):
    """Result summary of an import workflow run."""

    input_path: str
    total_raw: int
    drafted: int = 0  # LLM draft successes
    refined: int = 0  # expert-confirmed
    rejected: int = 0  # expert-discarded
    duplicates_skipped: int = 0
    validation_errors: int = 0
    added: int = 0  # actually written
    version: str = ""
    target_file: str = ""
    backup_file: Optional[str] = None
    degraded: bool = False  # ran without LLM
