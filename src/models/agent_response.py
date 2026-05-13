"""Pydantic models for agent HTTP API responses."""

from typing import Optional
from pydantic import BaseModel, Field


class DetectedConflict(BaseModel):
    """A conflict detected by the agent."""
    conflict_text: str
    conflict_type: str = ""
    confidence: float = 0.0


class GrammarCorrection(BaseModel):
    """A grammar correction made by the agent."""
    original_text: str
    corrected_text: str


class AgentResponse(BaseModel):
    """Structured response from the Defect Agent API."""
    sample_id: str = ""
    summary: str = ""
    detected_conflicts: list[DetectedConflict] = Field(default_factory=list)
    grammar_corrections: list[GrammarCorrection] = Field(default_factory=list)
    processing_time_ms: float = 0.0
    error: Optional[str] = None
    raw_response: Optional[dict] = None
