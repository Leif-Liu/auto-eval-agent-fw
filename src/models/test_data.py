"""Pydantic models for test data (Golden Test Set)."""

from enum import Enum, StrEnum
from pydantic import BaseModel, Field


class AnomalyType(str, Enum):
    E1_OVERSIZED = "E1_oversized"
    E2_GIBBERISH = "E2_gibberish"
    E3_FORMAT_ERROR = "E3_format_error"
    E4_MIXED_LANGUAGE = "E4_mixed_language"


class ConflictAnnotation(BaseModel):
    """A conflict annotation in the ground truth."""
    conflict_text: str
    conflict_type: str = ""  # e.g., "factual_contradiction", "data_mismatch"
    expected_detection: bool = True  # True if agent should flag this


class GrammarErrorAnnotation(BaseModel):
    """A grammar error annotation in the ground truth."""
    original_text: str
    corrected_text: str
    error_type: str = ""  # e.g., "typo", "tense", "punctuation"


class DifficultyLevel(StrEnum):
    """Difficulty levels for a standard test sample."""
    EASY = "easy"
    MEDIUM = "medium"
    COMPLEX = "complex"


class StandardSample(BaseModel):
    """A single test case in the golden test set."""
    sample_id: str
    input_description: str
    ground_truth_summary: str
    conflict_annotations: list[ConflictAnnotation] = Field(default_factory=list)
    grammar_error_annotations: list[GrammarErrorAnnotation] = Field(default_factory=list)
    difficulty: DifficultyLevel = DifficultyLevel.MEDIUM  # easy / medium / complex
    metadata: dict = Field(default_factory=dict)


class AnomalyCase(BaseModel):
    """An anomaly test case (E1-E4)."""
    case_id: str
    anomaly_type: AnomalyType
    input_description: str
    expected_behavior: str = ""  # e.g., "reject with error", "handle gracefully"


class IncrementalTurn(BaseModel):
    """One turn in an incremental test sequence."""
    turn_id: int
    input_text: str
    expected_new_info_count: int = 0


class IncrementalSequence(BaseModel):
    """A multi-turn incremental test sequence for reasoning efficiency."""
    sequence_id: str
    turns: list[IncrementalTurn]


class TestDataSet(BaseModel):
    """Complete test data loaded from all JSON files."""
    standard_samples: list[StandardSample] = Field(default_factory=list)
    anomaly_cases: list[AnomalyCase] = Field(default_factory=list)
    incremental_sequences: list[IncrementalSequence] = Field(default_factory=list)
