"""Score aggregation and maturity level calculation."""

from src.models.evaluation_result import DimensionScore, CompositeResult
from config import WEIGHTS, get_maturity_level


def compute_composite(dimension_scores: list[DimensionScore]) -> float:
    """Compute weighted sum of all dimension raw scores."""
    return round(sum(ds.weighted_score for ds in dimension_scores), 2)


def build_composite_result(
    dimension_scores: list[DimensionScore],
    run_id: str,
    timestamp: str,
    total_samples: int = 0,
    baseline: dict = None,
) -> CompositeResult:
    """Build a complete CompositeResult from dimension scores."""
    total_score = compute_composite(dimension_scores)
    maturity = get_maturity_level(total_score)

    baseline_comparison = None
    if baseline:
        baseline_comparison = {
            dim.dimension_name: {
                "current": dim.raw_score,
                "baseline": baseline.get(dim.dimension_name, 0),
                "delta": round(
                    dim.raw_score - baseline.get(dim.dimension_name, 0), 2
                ),
            }
            for dim in dimension_scores
        }
        baseline_comparison["total"] = {
            "current": total_score,
            "baseline": baseline.get("total", 0),
            "delta": round(total_score - baseline.get("total", 0), 2),
        }

    return CompositeResult(
        run_id=run_id,
        timestamp=timestamp,
        total_samples=total_samples,
        total_score=total_score,
        maturity_level=maturity,
        dimension_scores=dimension_scores,
        baseline_comparison=baseline_comparison,
    )
