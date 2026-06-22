"""Deterministic evolution metrics computed from historical evaluation runs.

All functions take a list of ``CompositeResult`` (any order — they sort by
timestamp internally) and return the Pydantic view-models from
``src/models/monitoring.py``. No I/O, no LLM calls: pure aggregation so the
dashboard layer can render them and feed a compact digest to the insight agent.
"""

import numpy as np

from config import MONITORING_SLOPE_MIN_RUNS
from src.models.evaluation_result import CompositeResult
from src.models.monitoring import (
    DimensionSlope,
    EvolutionDigest,
    MaturityStep,
    RunDelta,
)

# Maturity label -> ladder code. Matched by prefix so "L3 - Mature" -> 3.
_LEVEL_CODE = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}

# |slope| below this (points/run) is classified "flat" rather than up/down.
_FLAT_SLOPE_EPSILON = 0.5


def _level_code(label: str) -> int:
    for key, code in _LEVEL_CODE.items():
        if str(label).startswith(key):
            return code
    return 0


def _dim_score_map(result: CompositeResult) -> dict[str, float]:
    return {ds.dimension_name: ds.raw_score for ds in result.dimension_scores}


def _ordered(history: list[CompositeResult]) -> list[CompositeResult]:
    return sorted(history, key=lambda r: r.timestamp)


def compute_run_deltas(history: list[CompositeResult]) -> list[RunDelta]:
    """Score delta of each run vs the immediately previous run (oldest -> newest).

    The first run in the span has ``total_delta = None``; any dimension absent
    from the previous run also gets ``None`` for that run.
    """
    ordered = _ordered(history)
    prev_dims: dict[str, float] = {}
    prev_total: float | None = None
    out: list[RunDelta] = []

    for r in ordered:
        cur_dims = _dim_score_map(r)
        dim_deltas: dict[str, float | None] = {}
        for name, val in cur_dims.items():
            dim_deltas[name] = round(val - prev_dims[name], 2) if name in prev_dims else None

        total_delta = round(r.total_score - prev_total, 2) if prev_total is not None else None
        out.append(RunDelta(
            run_id=r.run_id,
            timestamp=r.timestamp,
            total_score=r.total_score,
            total_delta=total_delta,
            maturity_level=r.maturity_level,
            dimension_deltas=dim_deltas,
        ))
        prev_dims = cur_dims
        prev_total = r.total_score

    return out


def compute_maturity_trajectory(history: list[CompositeResult]) -> list[MaturityStep]:
    """Position of each run on the L1 -> L4 ladder (oldest -> newest)."""
    return [
        MaturityStep(
            run_id=r.run_id,
            timestamp=r.timestamp,
            level_code=_level_code(r.maturity_level),
            level_label=r.maturity_level,
            total_score=r.total_score,
        )
        for r in _ordered(history)
    ]


def _linear_fit(xs: np.ndarray, ys: np.ndarray) -> tuple[float, float, float]:
    """Ordinary least-squares fit -> (slope, intercept, r_squared)."""
    if len(xs) < 2 or len(ys) < 2:
        return 0.0, float(ys[0]) if len(ys) else 0.0, 0.0
    slope, intercept = (float(c) for c in np.polyfit(xs, ys, 1))
    pred = slope * xs + intercept
    ss_res = float(np.sum((ys - pred) ** 2))
    ss_tot = float(np.sum((ys - np.mean(ys)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, intercept, r_squared


def _trend_of(slope: float) -> str:
    if slope > _FLAT_SLOPE_EPSILON:
        return "improving"
    if slope < -_FLAT_SLOPE_EPSILON:
        return "declining"
    return "flat"


def compute_dimension_slopes(
    history: list[CompositeResult],
    min_runs: int = MONITORING_SLOPE_MIN_RUNS,
) -> list[DimensionSlope]:
    """Per-dimension linear-regression slope (points/run) across runs.

    Only dimensions present in *every* run contribute a slope (intermittent
    dimensions would bias the fit). Returns ``[]`` when fewer than ``min_runs``
    runs are available. Sorted by slope descending so the top improver is first.
    """
    ordered = _ordered(history)
    if len(ordered) < min_runs:
        return []

    xs = np.arange(len(ordered), dtype=float)

    # Dimension names in first-seen order; only those spanning all runs qualify.
    seen: list[str] = []
    for r in ordered:
        for ds in r.dimension_scores:
            if ds.dimension_name not in seen:
                seen.append(ds.dimension_name)

    out: list[DimensionSlope] = []
    for name in seen:
        series: list[float] = []
        for r in ordered:
            val = _dim_score_map(r).get(name)
            if val is None:
                series = []
                break
            series.append(val)
        if len(series) != len(ordered):
            continue  # dimension not present in every run

        ys = np.array(series, dtype=float)
        slope, intercept, r2 = _linear_fit(xs, ys)
        out.append(DimensionSlope(
            dimension_name=name,
            slope=round(slope, 2),
            intercept=round(intercept, 2),
            r_squared=round(r2, 3),
            n_runs=len(ordered),
            first_score=round(float(ys[0]), 2),
            last_score=round(float(ys[-1]), 2),
            trend=_trend_of(slope),
        ))

    out.sort(key=lambda s: s.slope, reverse=True)
    return out


def build_evolution_digest(
    history: list[CompositeResult],
    slopes: list[DimensionSlope],
) -> EvolutionDigest:
    """Compact quantitative summary for the LLM insight agent.

    Caller must guarantee ``history`` is non-empty.
    """
    ordered = _ordered(history)
    first, last = ordered[0], ordered[-1]

    deltas = compute_run_deltas(ordered)
    valid = [d for d in deltas if d.total_delta is not None]
    largest: dict | None = None
    if valid:
        big = max(valid, key=lambda d: abs(d.total_delta))
        largest = {"run_id": big.run_id, "delta": big.total_delta}

    top_improver = slopes[0].dimension_name if slopes and slopes[0].slope > 0 else None
    top_regressor = next((s.dimension_name for s in reversed(slopes) if s.slope < 0), None)

    return EvolutionDigest(
        n_runs=len(ordered),
        span_first_run_id=first.run_id,
        span_last_run_id=last.run_id,
        first_total=round(first.total_score, 2),
        last_total=round(last.total_score, 2),
        total_delta=round(last.total_score - first.total_score, 2),
        maturity_first=first.maturity_level,
        maturity_last=last.maturity_level,
        dimension_slopes=slopes,
        top_improver=top_improver,
        top_regressor=top_regressor,
        largest_run_delta=largest,
    )
