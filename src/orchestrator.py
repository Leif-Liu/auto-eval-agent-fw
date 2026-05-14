"""Main evaluation pipeline orchestrator."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import TEST_DATA_DIR, RESULTS_DIR
from src.data.loader import load_test_data, TestDataSet
from src.agent_client import AgentClient
from src.llm_judge import LLMJudge
from src.models.agent_response import AgentResponse
from src.evaluation import (
    summary_accuracy,
    conflict_detection,
    grammar_correction,
    system_stability,
    output_quality,
)
from src.reporting.score_calculator import build_composite_result
from src.reporting.report_generator import (
    write_json_report,
    load_previous_results,
    create_run_dir,
    print_summary,
)
from src.reporting.chart_builder import build_radar_chart, build_trend_chart

logger = logging.getLogger(__name__)


class EvaluationOrchestrator:
    """Coordinates the full evaluation pipeline."""

    def __init__(
        self,
        agent_client: AgentClient,
        llm_judge: LLMJudge,
        test_data_dir: Path = TEST_DATA_DIR,
        results_dir: Path = RESULTS_DIR,
    ):
        self.agent_client = agent_client
        self.llm_judge = llm_judge
        self.test_data_dir = Path(test_data_dir)
        self.results_dir = Path(results_dir)

    def run_full_evaluation(
        self,
        sample_limit: Optional[int] = None,
        dimensions: Optional[list[str]] = None,
        baseline_path: Optional[Path] = None,
    ) -> dict:
        """Main entry point. Loads data, calls agent, scores dimensions.

        Args:
            sample_limit: Max number of standard samples to evaluate.
            dimensions: Specific dimensions to run. None = all.
            baseline_path: Path to a baseline report JSON for comparison.

        Returns:
            Dict with run_dir, report path, chart paths, and summary text.
        """
        # Step 1: Load test data
        logger.info("Loading test data...")
        test_data = load_test_data(self.test_data_dir)
        samples = test_data.standard_samples[:sample_limit] if sample_limit else test_data.standard_samples
        logger.info(
            f"Loaded {len(samples)} standard samples, "
            f"{len(test_data.anomaly_cases)} anomaly cases, "
            f"{len(test_data.incremental_sequences)} incremental sequences"
        )

        # Step 2: Call agent for standard samples
        logger.info("Calling agent for standard samples...")
        sample_dicts = [s.model_dump() for s in samples]
        responses = self.agent_client.evaluate_batch(sample_dicts)
        successful = [r for r in responses if not r.error]
        logger.info(f"Agent responses: {len(successful)}/{len(responses)} successful")

        # Step 3: Call agent for anomaly cases
        logger.info("Calling agent for anomaly cases...")
        anomaly_dicts = [c.model_dump() for c in test_data.anomaly_cases]
        anomaly_responses = self.agent_client.evaluate_batch(anomaly_dicts)

        # Step 4: Run dimension evaluations
        logger.info("Running dimension evaluations...")
        all_dimensions = {
            "summary_accuracy": lambda: summary_accuracy.evaluate(responses, test_data, self.llm_judge),
            "conflict_detection": lambda: conflict_detection.evaluate(responses, test_data, self.llm_judge),
            "grammar_correction": lambda: grammar_correction.evaluate(responses, test_data, self.llm_judge),
            "system_stability": lambda: system_stability.evaluate(
                responses, test_data, anomaly_responses=anomaly_responses
            ),
            "output_quality": lambda: output_quality.evaluate(responses, test_data),
        }

        dims_to_run = dimensions if dimensions else list(all_dimensions.keys())
        dimension_scores = []
        for dim_name in dims_to_run:
            if dim_name in all_dimensions:
                logger.info(f"  Evaluating: {dim_name}")
                score = all_dimensions[dim_name]()
                dimension_scores.append(score)
            else:
                logger.warning(f"  Unknown dimension: {dim_name}")

        # Step 5: Build composite result
        now = datetime.now()
        run_dir, run_id = create_run_dir(self.results_dir)

        baseline = None
        if baseline_path:
            import json
            try:
                with open(baseline_path, encoding="utf-8") as f:
                    baseline_data = json.load(f)
                baseline = {
                    ds["dimension_name"]: ds["raw_score"]
                    for ds in baseline_data.get("dimension_scores", [])
                }
                baseline["total"] = baseline_data.get("total_score", 0)
            except Exception as e:
                logger.warning(f"Could not load baseline: {e}")

        result = build_composite_result(
            dimension_scores=dimension_scores,
            run_id=run_id,
            timestamp=now.isoformat(),
            total_samples=len(samples),
            baseline=baseline,
        )

        # Step 6: Generate reports
        logger.info("Generating reports...")
        report_path = write_json_report(result, run_dir)

        radar_path = None
        trend_path = None
        try:
            radar_path = build_radar_chart(
                dimension_scores,
                baseline_scores=baseline,
                output_path=run_dir / "radar_chart.png",
            )
        except Exception as e:
            logger.warning(f"Radar chart generation failed: {e}")

        try:
            history = load_previous_results(self.results_dir)
            if len(history) > 1:
                trend_path = build_trend_chart(
                    history,
                    output_path=run_dir / "trend_chart.png",
                )
        except Exception as e:
            logger.warning(f"Trend chart generation failed: {e}")

        summary = print_summary(result)

        return {
            "run_dir": str(run_dir),
            "report_path": str(report_path),
            "radar_chart": radar_path,
            "trend_chart": trend_path,
            "summary": summary,
            "result": result,
        }
