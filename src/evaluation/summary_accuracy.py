"""Summary Accuracy evaluation using LLM-as-a-Judge."""

from src.models.test_data import TestDataSet
from src.models.agent_response import AgentResponse
from src.models.evaluation_result import DimensionScore, SubScore
from src.evaluation.base import iter_paired_samples
from config import SUMMARY_WEIGHTS, WEIGHTS


def evaluate(
    agent_responses: list[AgentResponse],
    test_data: TestDataSet,
    llm_judge,
) -> DimensionScore:
    """Evaluate Summary Accuracy dimension using LLM-as-a-Judge.

    4 sub-dimensions: semantic_accuracy (0.35), field_correctness (0.30),
    summary_quality (0.20), completeness (0.15).
    """
    weight = WEIGHTS["summary_accuracy"]

    per_sample = []
    all_sub_scores = {k: [] for k in SUMMARY_WEIGHTS}

    # Track error responses separately
    for response in agent_responses:
        if response.error:
            per_sample.append({"sample_id": response.sample_id, "error": response.error, "score": 0})

    for response, sample in iter_paired_samples(agent_responses, test_data):
        # Call LLM judge
        result = llm_judge.judge_summary(
            ground_truth=sample.ground_truth_summary,
            agent_summary=response.summary,
        )

        # Calculate weighted score for this sample
        sample_score = sum(
            result.get(k, 0) * w for k, w in SUMMARY_WEIGHTS.items()
        )

        per_sample.append({
            "sample_id": response.sample_id,
            "score": round(sample_score, 2),
            "sub_scores": {k: result.get(k, 0) for k in SUMMARY_WEIGHTS},
            "reasoning": result.get("reasoning", ""),
        })

        for k in SUMMARY_WEIGHTS:
            all_sub_scores[k].append(result.get(k, 0))

    # Aggregate: average of per-sample scores
    valid_samples = [p for p in per_sample if "error" not in p]
    if not valid_samples:
        raw_score = 0.0
    else:
        raw_score = sum(p["score"] for p in valid_samples) / len(valid_samples)

    # Aggregate sub-scores
    sub_scores = []
    for name, w in SUMMARY_WEIGHTS.items():
        vals = all_sub_scores[name]
        avg = sum(vals) / len(vals) if vals else 0
        sub_scores.append(SubScore(name=name, score=round(avg, 2), weight=w))

    return DimensionScore(
        dimension_name="Summary Accuracy",
        weight=weight,
        raw_score=round(raw_score, 2),
        weighted_score=round(raw_score * weight, 2),
        sub_scores=sub_scores,
        per_sample_scores=per_sample,
        details={"samples_evaluated": len(valid_samples)},
    )
