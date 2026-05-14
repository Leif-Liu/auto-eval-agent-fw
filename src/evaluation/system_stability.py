"""System Stability evaluation — anomaly handling + reasoning efficiency."""

import logging
import statistics

from src.models.test_data import TestDataSet
from src.models.agent_response import AgentResponse
from src.models.evaluation_result import DimensionScore, SubScore
from config import WEIGHTS, STABILITY_WEIGHTS

logger = logging.getLogger(__name__)

GRACEFUL_INDICATORS = [
    "error", "invalid", "unable to", "please provide",
    "cannot process", "exceeds", "missing", "warning",
    "sorry", "could not", "not found", "无法", "错误",
    "输入", "格式", "超出",
]


def _is_graceful_response(response: AgentResponse) -> bool:
    """Check if agent handled an anomaly gracefully (text-based response)."""
    if response.error:
        return False

    summary = response.summary.lower() if response.summary else ""
    if not summary or len(summary.strip()) < 5:
        return False

    if any(ind in summary for ind in GRACEFUL_INDICATORS):
        return True

    if len(summary) > 50:
        has_fields = any(f in summary for f in ["[", "product", "vcu", "security", "build", "产品", "安全"])
        if has_fields:
            return True

    return False


def evaluate_anomaly_handling(
    anomaly_responses: list[AgentResponse],
) -> dict:
    """Evaluate how well the agent handles anomaly inputs (E1-E4)."""
    passed = 0
    details = []

    for resp in anomaly_responses:
        is_pass = _is_graceful_response(resp)
        if is_pass:
            passed += 1
        details.append({
            "sample_id": resp.sample_id,
            "passed": is_pass,
            "error": resp.error,
            "summary_length": len(resp.summary) if resp.summary else 0,
        })

    total = len(anomaly_responses)
    rate = passed / total if total > 0 else 0.0

    return {
        "handled_correctly": passed,
        "total": total,
        "rate": rate,
        "details": details,
    }


def evaluate_reasoning_efficiency(
    agent_responses: list[AgentResponse],
) -> dict:
    """Evaluate reasoning efficiency based on processing time consistency."""
    valid_responses = [r for r in agent_responses if not r.error and r.processing_time_ms > 0]
    if not valid_responses:
        return {"efficient_count": 0, "total": 0, "rate": 0.0, "details": []}

    times = [r.processing_time_ms for r in valid_responses]
    median_time = statistics.median(times)
    threshold = median_time * 3

    efficient = sum(1 for t in times if t <= threshold)
    total = len(times)
    rate = efficient / total if total > 0 else 1.0

    return {
        "efficient_count": efficient,
        "total": total,
        "rate": rate,
        "details": {
            "median_ms": round(median_time, 2),
            "threshold_ms": round(threshold, 2),
            "min_ms": round(min(times), 2),
            "max_ms": round(max(times), 2),
        },
    }


def evaluate(
    agent_responses: list[AgentResponse],
    test_data: TestDataSet,
    llm_judge=None,
    anomaly_responses: list[AgentResponse] = None,
) -> DimensionScore:
    """Evaluate System Stability dimension.

    Score = (anomaly_handling_rate * 0.5 + reasoning_efficiency_rate * 0.5) * 100
    """
    weight = WEIGHTS["system_stability"]

    if anomaly_responses is None:
        anomaly_responses = []
    anomaly_result = evaluate_anomaly_handling(anomaly_responses)
    efficiency_result = evaluate_reasoning_efficiency(agent_responses)

    anomaly_rate = anomaly_result["rate"]
    efficiency_rate = efficiency_result["rate"]
    raw_score = round(
        (anomaly_rate * STABILITY_WEIGHTS["anomaly_handling"]
         + efficiency_rate * STABILITY_WEIGHTS["reasoning_efficiency"]) * 100,
        2,
    )

    return DimensionScore(
        dimension_name="System Stability",
        weight=weight,
        raw_score=raw_score,
        weighted_score=round(raw_score * weight, 2),
        sub_scores=[
            SubScore(
                name="Anomaly Handling",
                score=round(anomaly_rate * 100, 2),
                weight=STABILITY_WEIGHTS["anomaly_handling"],
            ),
            SubScore(
                name="Reasoning Efficiency",
                score=round(efficiency_rate * 100, 2),
                weight=STABILITY_WEIGHTS["reasoning_efficiency"],
            ),
        ],
        per_sample_scores=anomaly_result["details"],
        details={
            "anomaly_handling": anomaly_result,
            "reasoning_efficiency": efficiency_result,
        },
    )
