"""Grammar Correction evaluation — uses LLM-as-a-Judge for text-based responses."""

import json
import logging

from src.models.test_data import TestDataSet
from src.models.agent_response import AgentResponse
from src.models.evaluation_result import DimensionScore, SubScore
from src.evaluation.base import iter_paired_samples
from config import WEIGHTS, GRAMMAR_WEIGHTS

logger = logging.getLogger(__name__)


def evaluate(
    agent_responses: list[AgentResponse],
    test_data: TestDataSet,
    llm_judge=None,
) -> DimensionScore:
    """Evaluate Grammar Correction dimension using LLM-as-a-Judge.

    Since the agent returns plain text, we use the LLM judge to determine
    whether the agent's response correctly fixes expected grammar errors.
    Score = fix_rate_score * 0.7 + overcorrection_score * 0.3
    """
    weight = WEIGHTS["grammar_correction"]

    if llm_judge is None:
        return DimensionScore(
            dimension_name="Grammar Correction",
            weight=weight,
            raw_score=0.0,
            weighted_score=0.0,
            sub_scores=[],
            details={"error": "LLM judge not provided"},
        )

    total_correctly_fixed = 0
    total_errors = 0
    total_over_corrections = 0
    per_sample = []

    for response in agent_responses:
        if response.error:
            per_sample.append({
                "sample_id": response.sample_id, "error": response.error,
            })

    for response, sample in iter_paired_samples(agent_responses, test_data):
        gt_errors = sample.grammar_error_annotations
        if not gt_errors:
            per_sample.append({
                "sample_id": response.sample_id,
                "correctly_fixed": 0,
                "total_errors": 0,
                "over_corrections": 0,
            })
            continue

        expected_json = json.dumps(
            [{"original_text": e.original_text, "corrected_text": e.corrected_text, "error_type": e.error_type}
             for e in gt_errors],
            ensure_ascii=False,
        )

        result = llm_judge.judge_grammar(
            input_description=sample.input_description,
            agent_response=response.summary,
            expected_errors=expected_json,
        )

        fixed = result.get("correctly_fixed", 0)
        errors = result.get("total_errors", len(gt_errors))
        over_corr = result.get("over_corrections", 0)

        total_correctly_fixed += fixed
        total_errors += errors
        total_over_corrections += over_corr

        fix_rate = fixed / errors if errors > 0 else 1.0
        per_sample.append({
            "sample_id": response.sample_id,
            "correctly_fixed": fixed,
            "total_errors": errors,
            "over_corrections": over_corr,
            "fix_rate": round(fix_rate, 4),
            "details": result.get("fix_details", []),
            "reasoning": result.get("reasoning", ""),
        })

    fix_rate = total_correctly_fixed / total_errors if total_errors > 0 else 1.0
    overcorrection_rate = total_over_corrections / max(total_errors, 1)

    fix_rate_score = fix_rate * 100
    overcorrection_score = max(0, (1 - overcorrection_rate)) * 100
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
            "total_over_corrections": total_over_corrections,
            "method": "LLM-as-a-Judge",
        },
    )
