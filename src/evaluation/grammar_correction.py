"""Grammar Correction evaluation — fix rate + over-correction rate."""

from src.models.test_data import TestDataSet
from src.models.agent_response import AgentResponse
from src.models.evaluation_result import DimensionScore, SubScore
from src.evaluation.base import iter_paired_samples
from config import WEIGHTS, GRAMMAR_WEIGHTS


def _align_corrections(
    ground_truth_errors: list[dict],
    agent_corrections: list[dict],
    original_text: str = "",
) -> dict:
    """Align agent corrections with ground truth errors.

    Returns: correctly_fixed, missed, over_corrected, total_errors, total_correct_words.
    """
    total_errors = len(ground_truth_errors)
    if total_errors == 0 and not agent_corrections:
        return {
            "correctly_fixed": 0,
            "missed": 0,
            "over_corrected": 0,
            "total_errors": 0,
            "total_correct_words": 0,
        }

    correctly_fixed = 0
    matched_corrections = set()

    for gt in ground_truth_errors:
        gt_original = gt.get("original_text", "").strip().lower()
        gt_corrected = gt.get("corrected_text", "").strip().lower()

        for i, ac in enumerate(agent_corrections):
            if i in matched_corrections:
                continue
            ac_original = ac.get("original_text", "").strip().lower()
            ac_corrected = ac.get("corrected_text", "").strip().lower()

            if gt_original == ac_original or gt_original in ac_original or ac_original in gt_original:
                if gt_corrected == ac_corrected or gt_corrected in ac_corrected:
                    correctly_fixed += 1
                    matched_corrections.add(i)
                    break

    missed = total_errors - correctly_fixed
    over_corrected = len(agent_corrections) - len(matched_corrections)

    total_correct_words = max(1, len(original_text.split()) - total_errors)

    return {
        "correctly_fixed": correctly_fixed,
        "missed": missed,
        "over_corrected": over_corrected,
        "total_errors": total_errors,
        "total_correct_words": total_correct_words,
    }


def evaluate(
    agent_responses: list[AgentResponse],
    test_data: TestDataSet,
    llm_judge=None,
) -> DimensionScore:
    """Evaluate Grammar Correction dimension.

    Score = fix_rate_score * 0.7 + overcorrection_score * 0.3
    where fix_rate_score = fix_rate * 100
          overcorrection_score = (1 - overcorrection_rate) * 100
    """
    weight = WEIGHTS["grammar_correction"]

    total_correctly_fixed = 0
    total_errors = 0
    total_over_corrected = 0
    total_correct_words = 0
    per_sample = []

    # Track error responses separately
    for response in agent_responses:
        if response.error:
            per_sample.append({
                "sample_id": response.sample_id, "error": response.error,
            })

    for response, sample in iter_paired_samples(agent_responses, test_data):
        gt_errors = [e.model_dump() for e in sample.grammar_error_annotations]
        agent_corrections = [c.model_dump() for c in response.grammar_corrections]

        result = _align_corrections(
            gt_errors, agent_corrections, sample.input_description
        )

        total_correctly_fixed += result["correctly_fixed"]
        total_errors += result["total_errors"]
        total_over_corrected += result["over_corrected"]
        total_correct_words += result["total_correct_words"]

        fix_rate = result["correctly_fixed"] / result["total_errors"] if result["total_errors"] > 0 else 1.0
        over_rate = result["over_corrected"] / result["total_correct_words"] if result["total_correct_words"] > 0 else 0.0

        per_sample.append({
            "sample_id": response.sample_id,
            "fix_rate": round(fix_rate, 4),
            "overcorrection_rate": round(over_rate, 4),
            "correctly_fixed": result["correctly_fixed"],
            "total_errors": result["total_errors"],
            "over_corrected": result["over_corrected"],
        })

    # Aggregate metrics
    fix_rate = total_correctly_fixed / total_errors if total_errors > 0 else 1.0
    overcorrection_rate = total_over_corrected / total_correct_words if total_correct_words > 0 else 0.0

    fix_rate_score = fix_rate * 100
    overcorrection_score = (1 - overcorrection_rate) * 100
    raw_score = round(
        fix_rate_score * GRAMMAR_WEIGHTS["fix_rate"]
        + overcorrection_score * GRAMMAR_WEIGHTS["overcorrection_rate"],
        2,
    )

    return DimensionScore(
        dimension_name="Grammar Correction",
        weight=weight,
        raw_score=raw_score,
        weighted_score=round(raw_score * weight, 2),
        sub_scores=[
            SubScore(name="Fix Rate", score=round(fix_rate_score, 2), weight=GRAMMAR_WEIGHTS["fix_rate"]),
            SubScore(name="Over-correction Penalty", score=round(overcorrection_score, 2), weight=GRAMMAR_WEIGHTS["overcorrection_rate"]),
        ],
        per_sample_scores=per_sample,
        details={
            "fix_rate": round(fix_rate, 4),
            "overcorrection_rate": round(overcorrection_rate, 4),
            "total_errors": total_errors,
            "total_correctly_fixed": total_correctly_fixed,
            "total_over_corrected": total_over_corrected,
        },
    )
