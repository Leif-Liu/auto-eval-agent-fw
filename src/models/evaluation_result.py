"""Pydantic models for evaluation results."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class MaturityLevel(str, Enum):
    L1 = "L1 - Initial"
    L2 = "L2 - Growing"
    L3 = "L3 - Mature"
    L4 = "L4 - Excellent"


class SubScore(BaseModel):
    """Score for one sub-dimension."""
    name: str
    score: float  # 0-100
    weight: float = 0.0
    reasoning: str = ""


class DimensionScore(BaseModel):
    """Score for one evaluation dimension."""
    dimension_name: str
    weight: float
    raw_score: float  # 0-100
    weighted_score: float  # raw_score * weight
    sub_scores: list[SubScore] = Field(default_factory=list)
    details: dict = Field(default_factory=dict)
    per_sample_scores: list[dict] = Field(default_factory=list)


class CompositeResult(BaseModel):
    """Complete evaluation result for one run."""
    run_id: str
    timestamp: str  # ISO 8601
    total_samples: int = 0
    total_score: float  # weighted sum, 0-100
    maturity_level: str
    dimension_scores: list[DimensionScore] = Field(default_factory=list)
    baseline_comparison: Optional[dict] = None
    metadata: dict = Field(default_factory=dict)
