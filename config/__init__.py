"""Centralized configuration loaded from environment variables."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# RAGFlow Agent
RAGFLOW_API_KEY = os.getenv("RAGFLOW_API_KEY", "")
RAGFLOW_BASE_URL = os.getenv("RAGFLOW_BASE_URL", "http://10.10.11.7:9380")
RAGFLOW_AGENT_ID = os.getenv("RAGFLOW_AGENT_ID", "")
AGENT_TIMEOUT_SEC = int(os.getenv("AGENT_TIMEOUT_SEC", "120"))

# OpenAI / vLLM (LLM Judge)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "vllm")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://10.10.11.7:11542/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "google/gemma-4-31B-it")
LLM_JUDGE_TEMPERATURE = 0.0

# Paths
TEST_DATA_DIR = Path(os.getenv("TEST_DATA_DIR", str(BASE_DIR / "test_data")))
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", str(BASE_DIR / "results")))

# Dataset flywheel tooling (test set expansion / re-evaluation)
DATASET_STAGING_DIR = Path(os.getenv("DATASET_STAGING_DIR", str(TEST_DATA_DIR / "_staging")))
DATASET_BACKUP_DIR = Path(os.getenv("DATASET_BACKUP_DIR", str(TEST_DATA_DIR / "_backups")))
DATASET_CHANGELOG_PATH = Path(os.getenv("DATASET_CHANGELOG_PATH", str(BASE_DIR / "CHANGELOG.md")))
DATASET_DEFAULT_VERSION = os.getenv("DATASET_DEFAULT_VERSION", "0.1.0")
DATASET_DEDUP_THRESHOLD = float(os.getenv("DATASET_DEDUP_THRESHOLD", "0.85"))

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
