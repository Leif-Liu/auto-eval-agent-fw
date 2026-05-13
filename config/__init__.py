"""Centralized configuration loaded from environment variables."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Agent API
AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8080")
AGENT_TIMEOUT_SEC = int(os.getenv("AGENT_TIMEOUT_SEC", "30"))

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
LLM_JUDGE_TEMPERATURE = 0.0

# Paths
TEST_DATA_DIR = Path(os.getenv("TEST_DATA_DIR", str(BASE_DIR / "test_data")))
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", str(BASE_DIR / "results")))

# Dimension weights (from proposal v3.0)
WEIGHTS = {
    "summary_accuracy": 0.30,
    "conflict_detection": 0.25,
    "grammar_correction": 0.20,
    "output_quality": 0.15,
    "system_stability": 0.10,
}

# Summary sub-dimension weights
SUMMARY_WEIGHTS = {
    "semantic_accuracy": 0.35,
    "field_correctness": 0.30,
    "summary_quality": 0.20,
    "completeness": 0.15,
}

# Grammar weights
GRAMMAR_WEIGHTS = {
    "fix_rate": 0.70,
    "overcorrection_rate": 0.30,
}

# Stability weights
STABILITY_WEIGHTS = {
    "anomaly_handling": 0.50,
    "reasoning_efficiency": 0.50,
}

# Maturity levels
MATURITY_LEVELS = [
    (90, "L4 - Excellent"),
    (75, "L3 - Mature"),
    (60, "L2 - Growing"),
    (0, "L1 - Initial"),
]


def get_maturity_level(score: float) -> str:
    """Determine maturity level from composite score."""
    for threshold, label in MATURITY_LEVELS:
        if score >= threshold:
            return label
    return "L1 - Initial"
