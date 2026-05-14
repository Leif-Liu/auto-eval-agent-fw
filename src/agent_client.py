"""RAGFlow SDK client to call the evaluated Master Agent."""

import logging
import time
from typing import Optional

from ragflow_sdk import RAGFlow

from src.models.agent_response import AgentResponse

logger = logging.getLogger(__name__)


class AgentClient:
    """Client wrapping RAGFlow SDK to interact with the evaluated agent."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        agent_id: str,
        timeout: int = 120,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.agent_id = agent_id
        self.timeout = timeout
        self.rag_object = None
        self.agent = None

    def connect(self) -> bool:
        """Initialize RAGFlow connection and verify the agent exists."""
        try:
            self.rag_object = RAGFlow(api_key=self.api_key, base_url=self.base_url)
            agents_list = self.rag_object.list_agents(id=self.agent_id)
            if not agents_list:
                logger.error(f"No agent found with ID '{self.agent_id}'")
                return False
            self.agent = agents_list[0]
            logger.info(f"Connected to agent: {getattr(self.agent, 'name', 'Unknown')} (ID: {self.agent.id})")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to RAGFlow: {e}")
            return False

    def evaluate_sample(self, sample_id: str, description: str) -> AgentResponse:
        """Send a question to the RAGFlow agent and return the response.

        Creates a fresh session for each sample to ensure isolation.
        """
        start_time = time.time()
        try:
            if not self.agent:
                return AgentResponse(sample_id=sample_id, error="AgentNotConnected")

            session = self.agent.create_session()
            content = ""
            for ans in session.ask(description, stream=True):
                content = ans.content

            elapsed_ms = (time.time() - start_time) * 1000
            return AgentResponse(
                sample_id=sample_id,
                summary=content,
                processing_time_ms=round(elapsed_ms, 2),
                raw_response={"content": content},
            )
        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            logger.error(f"Error evaluating sample {sample_id}: {e}")
            return AgentResponse(
                sample_id=sample_id,
                error=str(type(e).__name__),
                processing_time_ms=round(elapsed_ms, 2),
            )

    def evaluate_batch(
        self,
        samples: list[dict],
        progress_callback: Optional[callable] = None,
    ) -> list[AgentResponse]:
        """Evaluate multiple samples sequentially, each in an isolated session."""
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
        """Verify RAGFlow and the target agent are reachable."""
        try:
            if not self.rag_object:
                self.rag_object = RAGFlow(api_key=self.api_key, base_url=self.base_url)
            agents = self.rag_object.list_agents(id=self.agent_id)
            return len(agents) > 0
        except Exception:
            return False
