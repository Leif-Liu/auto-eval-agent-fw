"""Output Quality evaluation — placeholder for human evaluation.

This dimension requires human expert blind review and is not automated.
Returns a placeholder score of 0.
"""

from src.models.test_data import TestDataSet
from src.models.agent_response import AgentResponse
from src.models.evaluation_result import DimensionScore
from config import WEIGHTS


def evaluate(
    agent_responses: list[AgentResponse],
    test_data: TestDataSet,
    llm_judge=None,
) -> DimensionScore:
    """Placeholder for Output Quality dimension (human evaluation).

    Score formula: fluency * 0.3 + professionalism * 0.5 + format * 0.2
    This requires 2-3 domain experts for blind review.
    """
    weight = WEIGHTS["output_quality"]

    return DimensionScore(
        dimension_name="Output Quality",
        weight=weight,
        raw_score=0.0,
        weighted_score=0.0,
        sub_scores=[],
        details={
            "status": "requires_human_evaluation",
            "method": "Expert blind review (2-3 experts)",
            "sub_dimensions": {
                "fluency": {"weight": 0.3, "description": "语句通顺、可读性好"},
                "professionalism": {"weight": 0.5, "description": "术语准确、内容专业"},
                "format": {"weight": 0.2, "description": "格式统一、规范"},
            },
            "note": "Run human evaluation separately and input scores via CLI.",
        },
    )
