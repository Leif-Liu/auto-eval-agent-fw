"""Conflict Detection evaluation — uses LLM-as-a-Judge for text-based responses."""

import json
import logging

from src.models.test_data import TestDataSet
from src.models.agent_response import AgentResponse
from src.models.evaluation_result import DimensionScore, SubScore
from src.evaluation.base import iter_paired_samples
from config import WEIGHTS

logger = logging.getLogger(__name__)


def evaluate(
    agent_responses: list[AgentResponse],
    test_data: TestDataSet,
    llm_judge=None,
) -> DimensionScore:
    """Evaluate Conflict Detection dimension using LLM-as-a-Judge.

    Since the agent returns plain text, we use the LLM judge to determine
    whether the agent's response correctly identifies expected conflicts.
    Score = F1-derived metric * 100.
    """
    weight = WEIGHTS["conflict_detection"]

    if llm_judge is None:
        return DimensionScore(
            dimension_name="Conflict Detection",
            weight=weight,
            raw_score=0.0,
            weighted_score=0.0,
            sub_scores=[],
            details={"error": "LLM judge not provided"},
        )

    total_detected = 0
    total_expected = 0
    total_false_positives = 0
    per_sample = []

    for response in agent_responses:
        if response.error:
            per_sample.append({
                "sample_id": response.sample_id,
                "error": response.error,
                "detected": 0, "expected": 0, "false_positives": 0,
            })

    for response, sample in iter_paired_samples(agent_responses, test_data):
        gt_conflicts = [c for c in sample.conflict_annotations if c.expected_detection]
        expected_json = json.dumps(
            [{"conflict_text": c.conflict_text, "conflict_type": c.conflict_type} for c in gt_conflicts],
            ensure_ascii=False,
        )

        result = llm_judge.judge_conflicts(
            input_description=sample.input_description,
            agent_response=response.summary,
            expected_conflicts=expected_json,
        )

        detected = result.get("detected_count", 0)
        expected = result.get("total_expected", len(gt_conflicts))
        false_pos = result.get("false_positives", 0)

        total_detected += detected
        total_expected += expected
        total_false_positives += false_pos

        per_sample.append({
            "sample_id": response.sample_id,
            "detected": detected,
            "expected": expected,
            "false_positives": false_pos,
            "details": result.get("detection_details", []),
            "reasoning": result.get("reasoning", ""),
        })

    # Compute precision / recall / F1
    tp = total_detected
    fp = total_false_positives
    fn = total_expected - total_detected

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    raw_score = round(f1 * 100, 2)

    return DimensionScore(
        dimension_name="Conflict Detection",
        weight=weight,
        raw_score=raw_score,
        weighted_score=round(raw_score * weight, 2),
        sub_scores=[
            SubScore(name="Precision", score=round(precision * 100, 2), weight=0.5),
            SubScore(name="Recall", score=round(recall * 100, 2), weight=0.5),
            SubScore(name="F1", score=raw_score, weight=1.0),
        ],
        per_sample_scores=per_sample,
        details={
            "TP": tp, "FP": fp, "FN": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "method": "LLM-as-a-Judge",
        },
    )
