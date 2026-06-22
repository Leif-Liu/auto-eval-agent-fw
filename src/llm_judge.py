"""LLM-as-a-Judge wrapper using OpenAI-compatible API (vLLM)."""

import json
import logging
import re
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

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

JUDGE_CONFLICT_PROMPT = """You are an expert evaluator for conflict detection in defect descriptions.

[Original Input Description]: {input_description}
[Agent Response]: {agent_response}
[Expected Conflicts]: {expected_conflicts}

The expected conflicts are issues that should be detected in the input. Analyze the agent's response and determine:
1. For each expected conflict, did the agent correctly identify it? (detected: true/false)
2. Did the agent report any conflicts that are NOT in the expected list? (false positives)

Return ONLY a JSON object in this exact format:
{{"detected_count": <number of correctly detected conflicts>, "total_expected": <total expected conflicts>, "false_positives": <number of false positive detections>, "detection_details": [{{"conflict": "<conflict text>", "detected": true/false, "reason": "<brief explanation>"}}], "reasoning": "<brief overall explanation>"}}
"""

JUDGE_GRAMMAR_PROMPT = """You are an expert evaluator for grammar correction in defect descriptions.

[Original Input Description]: {input_description}
[Agent Response]: {agent_response}
[Expected Grammar Errors]: {expected_errors}

The expected grammar errors are issues that should be corrected in the agent's output. Analyze the agent's response and determine:
1. For each expected error, did the agent correctly fix it? (fixed: true/false)
2. Did the agent make any unnecessary corrections to text that was already correct? (over_corrections)

Return ONLY a JSON object in this exact format:
{{"correctly_fixed": <number of correctly fixed errors>, "total_errors": <total expected errors>, "over_corrections": <number of unnecessary changes>, "fix_details": [{{"original": "<original text>", "corrected": "<expected correction>", "fixed": true/false, "reason": "<brief explanation>"}}], "reasoning": "<brief overall explanation>"}}
"""

JUDGE_DRAFT_STANDARD_PROMPT = """You are an expert annotation assistant for the Defect Description Agent test set.
Given a raw production input and the agent's actual response, draft a candidate StandardSample that a human expert will review and refine.

[Raw Input Description]: {input_description}
[Agent Actual Response]: {agent_response}

Produce a candidate by following these rules:
1. ground_truth_summary: Re-format using the canonical header [Product Line][VCU][Security Level][Build Flavor][Vehicle Program] followed by a concise one-sentence defect description. Extract field values ONLY from the input; use "Unknown" for missing fields. Do NOT invent facts.
2. conflict_annotations: List internal contradictions in the input that the agent should be expected to detect. Each item: {{"conflict_text": "<quote>", "conflict_type": "factual_contradiction|data_mismatch|partial_inconsistency|sensor_conflict", "expected_detection": true}}. Use [] if none.
3. grammar_error_annotations: List grammar/style issues in the RAW INPUT (not the agent output) that a correct agent should fix. Each item: {{"original_text": "<quote>", "corrected_text": "<fix>", "error_type": "typo|tense|punctuation|style|grammar"}}. Use [] if none.
4. difficulty: "easy" if single-system single-symptom; "medium" if 2 systems or 1 conflict; "complex" if multi-system, sensor conflicts, or safety-critical.
5. draft_confidence: 0.0-1.0 — how confident you are this draft needs no edits.
6. draft_notes: one-line note to the human reviewer highlighting uncertainties.

Return ONLY a JSON object in this exact format:
{{"ground_truth_summary": "...", "conflict_annotations": [...], "grammar_error_annotations": [...], "difficulty": "easy|medium|complex", "draft_confidence": <0-1>, "draft_notes": "..."}}
"""

EVOLUTION_INSIGHT_PROMPT = """你是 Defect Description Agent 的能力演进分析师。下面是该 Agent 历次完整评估 run 的量化摘要（每轮 = 一次全量评估）。请只依据这些数据，用**简体中文**写一段客观、数据驱动的演进分析，不要编造未给出的数字。

[评估历史量化摘要]
{digest}

请严格按以下结构输出（使用 Markdown 小标题，不要输出 JSON）：

## 总体趋势
一句话总结从首轮到末轮总分与成熟度的变化，给出净增/降幅度。

## 维度改进与回退
依据各维度的改进斜率(slope)：逐维度说明谁在持续进步、谁在停滞、谁在回退，引用具体斜率与首末分数。

## 关键拐点
若存在单轮波动最大的 run，指出其 run_id、波动幅度及可能涉及的维度；若无显著拐点则说明趋势平稳。

## 下一步优化建议
给出 2-3 条最值得投入的优化方向，按预期收益排序。

要求：语言精炼，全文控制在 300 字以内。
"""


class LLMJudge:
    """Wrapper around OpenAI-compatible API for LLM-as-a-Judge evaluation."""

    def __init__(
        self,
        api_key: str,
        base_url: str = None,
        model: str = "google/gemma-4-31B-it",
        temperature: float = 0.0,
    ):
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model = model
        self.temperature = temperature
        logger.info(f"LLM Judge initialized: model={model}, base_url={base_url}")

    def judge_summary(
        self,
        ground_truth: str,
        agent_summary: str,
        retries: int = 2,
    ) -> dict:
        """Judge a single summary against ground truth."""
        prompt = JUDGE_SUMMARY_PROMPT.format(
            ground_truth=ground_truth,
            agent_summary=agent_summary,
        )
        return self._call_with_retry(prompt, retries)

    def judge_conflicts(
        self,
        input_description: str,
        agent_response: str,
        expected_conflicts: str,
        retries: int = 2,
    ) -> dict:
        """Judge conflict detection quality from the agent's text response."""
        prompt = JUDGE_CONFLICT_PROMPT.format(
            input_description=input_description,
            agent_response=agent_response,
            expected_conflicts=expected_conflicts,
        )
        return self._call_with_retry(prompt, retries)

    def judge_grammar(
        self,
        input_description: str,
        agent_response: str,
        expected_errors: str,
        retries: int = 2,
    ) -> dict:
        """Judge grammar correction quality from the agent's text response."""
        prompt = JUDGE_GRAMMAR_PROMPT.format(
            input_description=input_description,
            agent_response=agent_response,
            expected_errors=expected_errors,
        )
        return self._call_with_retry(prompt, retries)

    def judge_draft_standard_sample(
        self,
        input_description: str,
        agent_response: str,
        retries: int = 2,
    ) -> dict:
        """Draft a candidate StandardSample from a raw case (LLM-assisted annotation).

        This is the reverse application of LLM-as-a-Judge: from a raw production
        case, propose the ground truth a human expert should refine. Returns the
        parsed JSON dict. On failure the dict is the default error shape (no
        ``ground_truth_summary`` key); callers detect this and degrade to manual
        annotation.
        """
        prompt = JUDGE_DRAFT_STANDARD_PROMPT.format(
            input_description=input_description,
            agent_response=agent_response,
        )
        return self._call_with_retry(prompt, retries)

    def judge_evolution(
        self,
        digest: str,
        retries: int = 2,
    ) -> str:
        """Generate a Chinese narrative analysis of agent capability evolution.

        Reads a compact quantitative digest of historical runs and returns
        free-form Markdown prose (NOT JSON), so it uses a plain text-completion
        path rather than the structured-output fallback machinery. Returns an
        empty string on failure so callers can degrade gracefully.
        """
        prompt = EVOLUTION_INSIGHT_PROMPT.format(digest=digest)
        return self._call_text_with_retry(prompt, retries)

    def judge_batch(
        self,
        pairs: list[tuple[str, str]],
        progress_callback: Optional[callable] = None,
    ) -> list[dict]:
        """Judge multiple summary pairs sequentially."""
        results = []
        for i, (gt, agent_sum) in enumerate(pairs):
            result = self.judge_summary(gt, agent_sum)
            results.append(result)
            if progress_callback:
                progress_callback(i + 1, len(pairs))
        return results

    def _call_raw(self, prompt: str, retries: int = 2) -> str:
        """Call the LLM and return the stripped raw content, with retry.

        Shared by the structured (JSON) and free-text judges. Returns "" on
        failure so callers degrade consistently; the exception detail is kept
        in the warning log.
        """
        for attempt in range(retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.choices[0].message.content.strip()
            except Exception as e:
                logger.warning(f"LLM attempt {attempt + 1} failed: {e}")
                if attempt == retries:
                    return ""
        return ""

    def _call_with_retry(self, prompt: str, retries: int = 2) -> dict:
        """Call the LLM with retry, then parse the JSON response."""
        content = self._call_raw(prompt, retries)
        if not content:
            return self._error_result("Empty or failed response")
        return self._parse_json_response(content)

    def _call_text_with_retry(self, prompt: str, retries: int = 2) -> str:
        """Call the LLM and return raw text (no JSON parsing), with retry."""
        return self._call_raw(prompt, retries)

    def _parse_json_response(self, content: str) -> dict:
        """Parse JSON from LLM response, with fallback extraction."""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

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
