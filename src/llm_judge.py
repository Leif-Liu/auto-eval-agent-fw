"""OpenAI LLM-as-a-Judge wrapper for evaluation."""

import json
import logging
import re
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

# Judge prompt template for Summary Accuracy evaluation
JUDGE_SUMMARY_PROMPT = """You are an expert evaluator for Defect Description Agent outputs.
Compare the [Ground Truth Summary] with the [Agent Generated Summary] and score on a 0-100 scale across these dimensions:

1. Semantic Accuracy: Does the Agent Summary accurately convey the core meaning of the Ground Truth? Are there semantic deviations or misunderstandings?
2. Field Correctness: Are structured fields (Product Line, VCU, Security Level, Build Flavor, Vehicle Program) correctly extracted and consistent with the Ground Truth?
3. Summary Quality: Is the defect description part concise and accurate? Is it semantically equivalent to the Ground Truth's expression?
4. Completeness: Is any key information from the Ground Truth missing? Is any information introduced that doesn't exist in the Ground Truth?

[Ground Truth Summary]: {ground_truth}
[Agent Generated Summary]: {agent_summary}

Return ONLY a JSON object in this exact format:
{{"semantic_accuracy": <0-100>, "field_correctness": <0-100>, "summary_quality": <0-100>, "completeness": <0-100>, "reasoning": "<brief explanation>"}}
"""


class LLMJudge:
    """Wrapper around OpenAI for LLM-as-a-Judge evaluation."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        temperature: float = 0.0,
    ):
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.temperature = temperature

    def judge_summary(
        self,
        ground_truth: str,
        agent_summary: str,
        retries: int = 2,
    ) -> dict:
        """Judge a single summary against ground truth.

        Returns dict with keys: semantic_accuracy, field_correctness,
        summary_quality, completeness, reasoning.
        """
        prompt = JUDGE_SUMMARY_PROMPT.format(
            ground_truth=ground_truth,
            agent_summary=agent_summary,
        )

        for attempt in range(retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    messages=[{"role": "user", "content": prompt}],
                )
                content = response.choices[0].message.content.strip()
                return self._parse_json_response(content)
            except Exception as e:
                logger.warning(f"LLM judge attempt {attempt + 1} failed: {e}")
                if attempt == retries:
                    return self._error_result(str(e))

        return self._error_result("Max retries exceeded")

    def judge_batch(
        self,
        pairs: list[tuple[str, str]],
        progress_callback: Optional[callable] = None,
    ) -> list[dict]:
        """Judge multiple summary pairs sequentially.

        Args:
            pairs: list of (ground_truth, agent_summary) tuples
        """
        results = []
        for i, (gt, agent_sum) in enumerate(pairs):
            result = self.judge_summary(gt, agent_sum)
            results.append(result)
            if progress_callback:
                progress_callback(i + 1, len(pairs))
        return results

    def _parse_json_response(self, content: str) -> dict:
        """Parse JSON from LLM response, with fallback extraction."""
        # Try direct JSON parse
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from markdown code block
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding first { ... } block
        brace_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", content, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        logger.warning(f"Failed to parse LLM response as JSON: {content[:200]}")
        return self._error_result(f"Could not parse response: {content[:100]}")

    def _error_result(self, error_msg: str) -> dict:
        """Return a default error result."""
        return {
            "semantic_accuracy": 0,
            "field_correctness": 0,
            "summary_quality": 0,
            "completeness": 0,
            "reasoning": f"Evaluation failed: {error_msg}",
        }
