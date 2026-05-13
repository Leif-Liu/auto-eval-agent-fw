"""HTTP client to call the Defect Description Agent API."""

import logging
from typing import Optional

import requests

from src.models.agent_response import AgentResponse

logger = logging.getLogger(__name__)


class AgentClient:
    """Thin HTTP client wrapping requests to call the Defect Agent API."""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def evaluate_sample(self, sample_id: str, description: str) -> AgentResponse:
        """POST defect description to agent, return structured response."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/defect/process",
                json={"defect_description": description},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return self._parse_response(sample_id, data)
        except requests.Timeout:
            logger.warning(f"Timeout for sample {sample_id}")
            return AgentResponse(sample_id=sample_id, error="TimeoutError")
        except requests.ConnectionError:
            logger.error(f"Connection error for sample {sample_id}")
            return AgentResponse(sample_id=sample_id, error="ConnectionError")
        except requests.HTTPError as e:
            logger.warning(f"HTTP error {e.response.status_code} for sample {sample_id}")
            return AgentResponse(
                sample_id=sample_id,
                error=f"HTTPError: {e.response.status_code}",
            )
        except Exception as e:
            logger.error(f"Unexpected error for sample {sample_id}: {e}")
            return AgentResponse(sample_id=sample_id, error=str(type(e).__name__))

    def evaluate_batch(
        self,
        samples: list[dict],
        progress_callback: Optional[callable] = None,
    ) -> list[AgentResponse]:
        """Evaluate multiple samples sequentially."""
        results = []
        for i, sample in enumerate(samples):
            response = self.evaluate_sample(
                sample_id=sample.get("sample_id", sample.get("case_id", f"batch-{i}")),
                description=sample.get("input_description", sample.get("input_text", "")),
            )
            results.append(response)
            if progress_callback:
                progress_callback(i + 1, len(samples))
        return results

    def health_check(self) -> bool:
        """Verify agent is reachable."""
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def _parse_response(self, sample_id: str, data: dict) -> AgentResponse:
        """Parse raw API response into AgentResponse model."""
        from src.models.agent_response import DetectedConflict, GrammarCorrection

        conflicts = [
            DetectedConflict(**c) for c in data.get("detected_conflicts", [])
        ]
        corrections = [
            GrammarCorrection(**c) for c in data.get("grammar_corrections", [])
        ]

        return AgentResponse(
            sample_id=sample_id,
            summary=data.get("summary", ""),
            detected_conflicts=conflicts,
            grammar_corrections=corrections,
            processing_time_ms=data.get("processing_time_ms", 0.0),
            error=data.get("error"),
            raw_response=data,
        )
