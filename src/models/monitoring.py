"""Pydantic models for evolution-dashboard metrics.

These are *derived* views computed from historical ``CompositeResult`` runs
(see ``src/monitoring/metrics.py``) and rendered by the dashboard. They are not
the canonical evaluation record — that lives in ``evaluation_result.py`` — but
transient quantitative summaries of how the evaluated agent has evolved across
iterations.
"""

from typing import Optional

from pydantic import BaseModel, Field


class RunDelta(BaseModel):
    """One run's scores vs the immediately previous run (oldest -> newest)."""

    run_id: str
    timestamp: str
    total_score: float
    total_delta: Optional[float] = None  # None for the first run in the span
    maturity_level: str
    dimension_deltas: dict[str, Optional[float]] = Field(default_factory=dict)


class MaturityStep(BaseModel):
    """One run's position on the L1 -> L4 maturity ladder."""

    run_id: str
    timestamp: str
    level_code: int  # 1..4 (0 if unparseable)
    level_label: str
    total_score: float


class DimensionSlope(BaseModel):
    """Linear-regression improvement rate for one dimension across runs."""

    dimension_name: str
    slope: float  # points per run
    intercept: float
    r_squared: float  # goodness of fit, 0..1
    n_runs: int
    first_score: float
    last_score: float
    trend: str  # "improving" | "flat" | "declining"


class EvolutionDigest(BaseModel):
    """Compact quantitative summary fed to the LLM insight agent."""

    n_runs: int
    span_first_run_id: str
    span_last_run_id: str
    first_total: float
    last_total: float
    total_delta: float
    maturity_first: str
    maturity_last: str
    dimension_slopes: list[DimensionSlope] = Field(default_factory=list)
    top_improver: Optional[str] = None
    top_regressor: Optional[str] = None
    largest_run_delta: Optional[dict] = None  # {"run_id": str, "delta": float}
