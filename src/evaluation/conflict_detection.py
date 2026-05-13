"""Conflict Detection evaluation — TP/FP/FN/TN → F1 score."""

from src.models.test_data import TestDataSet
from src.models.agent_response import AgentResponse
from src.models.evaluation_result import DimensionScore, SubScore
from src.evaluation.base import iter_paired_samples
from config import WEIGHTS


def _match_conflicts(
    ground_truth_conflicts: list[dict],
    detected_conflicts: list[dict],
) -> dict:
    """Match detected conflicts against ground truth.

    Uses text overlap for matching. Returns TP, FP, FN counts.
    """
    if not ground_truth_conflicts and not detected_conflicts:
        return {"TP": 0, "FP": 0, "FN": 0, "TN": 1}

    if not ground_truth_conflicts and detected_conflicts:
        return {"TP": 0, "FP": len(detected_conflicts), "FN": 0, "TN": 0}

    if ground_truth_conflicts and not detected_conflicts:
        return {"TP": 0, "FP": 0, "FN": len(ground_truth_conflicts), "TN": 0}

    # Both have conflicts — match by text overlap
    matched_gt = set()
    matched_det = set()

    for i, det in enumerate(detected_conflicts):
        det_text = det.get("conflict_text", "").lower()
        for j, gt in enumerate(ground_truth_conflicts):
            if j in matched_gt:
                continue
            gt_text = gt.get("conflict_text", "") if isinstance(gt, dict) else gt.conflict_text
            gt_text = gt_text.lower()
            if det_text and gt_text and (det_text in gt_text or gt_text in det_text):
                matched_gt.add(j)
                matched_det.add(i)
                break

    tp = len(matched_gt)
    fp = len(detected_conflicts) - len(matched_det)
    fn = len(ground_truth_conflicts) - len(matched_gt)

    return {"TP": tp, "FP": fp, "FN": fn, "TN": 0}


def _compute_f1(tp: int, fp: int, fn: int) -> dict:
    """Compute precision, recall, F1 from confusion matrix counts.

    Returns dict with precision, recall, f1.
    """
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    if precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * (precision * recall) / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1}


def evaluate(
    agent_responses: list[AgentResponse],
    test_data: TestDataSet,
    llm_judge=None,
) -> DimensionScore:
    """Evaluate Conflict Detection dimension.

    Score = F1 × 100.
    """
    weight = WEIGHTS["conflict_detection"]

    total_tp, total_fp, total_fn = 0, 0, 0
    per_sample = []

    # Track error responses separately
    for response in agent_responses:
        if response.error:
            per_sample.append({
                "sample_id": response.sample_id,
                "error": response.error,
                "TP": 0, "FP": 0, "FN": 0,
            })

    for response, sample in iter_paired_samples(agent_responses, test_data):
        gt_conflicts = [c.model_dump() for c in sample.conflict_annotations
                        if c.expected_detection]
        det_conflicts = [c.model_dump() for c in response.detected_conflicts]

        result = _match_conflicts(gt_conflicts, det_conflicts)
        total_tp += result["TP"]
        total_fp += result["FP"]
        total_fn += result["FN"]

        per_sample.append({
            "sample_id": response.sample_id,
            "TP": result["TP"],
            "FP": result["FP"],
            "FN": result["FN"],
            "gt_count": len(gt_conflicts),
            "det_count": len(det_conflicts),
        })

    metrics = _compute_f1(total_tp, total_fp, total_fn)
    raw_score = round(metrics["f1"] * 100, 2)

    return DimensionScore(
        dimension_name="Conflict Detection",
        weight=weight,
        raw_score=raw_score,
        weighted_score=round(raw_score * weight, 2),
        sub_scores=[
            SubScore(name="Precision", score=round(metrics["precision"] * 100, 2), weight=0.5),
            SubScore(name="Recall", score=round(metrics["recall"] * 100, 2), weight=0.5),
            SubScore(name="F1", score=raw_score, weight=1.0),
        ],
        per_sample_scores=per_sample,
        details={
            "TP": total_tp, "FP": total_fp, "FN": total_fn,
            "precision": round(metrics["precision"], 4),
            "recall": round(metrics["recall"], 4),
            "f1": round(metrics["f1"], 4),
        },
    )
