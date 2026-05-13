"""Shared utilities for evaluation modules."""

from src.models.test_data import TestDataSet, StandardSample
from src.models.agent_response import AgentResponse


def build_sample_index(test_data: TestDataSet) -> dict[str, StandardSample]:
    """Build a sample_id -> StandardSample lookup dict."""
    return {s.sample_id: s for s in test_data.standard_samples}


def iter_paired_samples(
    agent_responses: list[AgentResponse],
    test_data: TestDataSet,
):
    """Yield (response, sample) pairs, skipping errors and missing samples."""
    samples_by_id = build_sample_index(test_data)
    for response in agent_responses:
        if response.error:
            continue
        sample = samples_by_id.get(response.sample_id)
        if sample:
            yield response, sample
