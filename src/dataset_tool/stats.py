"""Distribution stats + balance suggestions for standard samples."""

from src.models.dataset_draft import DistributionReport
from src.models.test_data import StandardSample

# Target distribution (proposal STEP7): easy:medium:complex ≈ 3:4:3.
TARGET_DIFFICULTY = {"easy": 0.3, "medium": 0.4, "complex": 0.3}

# Summary length buckets, in characters.
SHORT_MAX = 120
MEDIUM_MAX = 240


def _length_bucket(length: int) -> str:
    if length <= SHORT_MAX:
        return "short"
    if length <= MEDIUM_MAX:
        return "medium"
    return "long"


def _density_key(count: int) -> str:
    return "3+" if count >= 3 else str(count)


def describe_distribution(samples: list[StandardSample]) -> DistributionReport:
    """Compute a distribution snapshot over a set of standard samples."""
    by_difficulty: dict[str, int] = {}
    by_source: dict[str, int] = {}
    length_buckets: dict[str, int] = {}
    conflict_density: dict[str, int] = {}
    grammar_density: dict[str, int] = {}
    structured_header = 0

    for s in samples:
        level = str(s.difficulty)
        by_difficulty[level] = by_difficulty.get(level, 0) + 1

        source = (s.metadata or {}).get("source", "unknown")
        by_source[source] = by_source.get(source, 0) + 1

        bucket = _length_bucket(len(s.ground_truth_summary or ""))
        length_buckets[bucket] = length_buckets.get(bucket, 0) + 1

        ck = _density_key(len(s.conflict_annotations or []))
        conflict_density[ck] = conflict_density.get(ck, 0) + 1

        gk = _density_key(len(s.grammar_error_annotations or []))
        grammar_density[gk] = grammar_density.get(gk, 0) + 1

        # Structured header proxy: canonical GT starts with [Field][Field]...
        if (s.ground_truth_summary or "").count("[") >= 2:
            structured_header += 1

    return DistributionReport(
        total=len(samples),
        by_difficulty=by_difficulty,
        by_metadata_source=by_source,
        summary_length_buckets=length_buckets,
        conflict_density=conflict_density,
        grammar_density=grammar_density,
        field_coverage={"structured_header": structured_header},
    )


def suggest_balance_action(report: DistributionReport) -> list[str]:
    """Warn-only text hints toward the 3:4:3 difficulty target. Empty if balanced."""
    hints: list[str] = []
    total = report.total or 1
    for level, target in TARGET_DIFFICULTY.items():
        actual = report.by_difficulty.get(level, 0) / total
        if actual < target - 0.08:
            hints.append(
                f"{level} 占比 {actual:.0%} 低于目标 {target:.0%}，建议补充"
            )
    return hints
