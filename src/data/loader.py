"""Load and validate test data from JSON files."""

import json
from pathlib import Path

from src.models.test_data import (
    StandardSample,
    AnomalyCase,
    IncrementalSequence,
    TestDataSet,
)


def load_standard_samples(filepath: Path) -> list[StandardSample]:
    """Load standard test samples from JSON file."""
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    return [StandardSample(**s) for s in data]


def load_anomaly_cases(filepath: Path) -> list[AnomalyCase]:
    """Load anomaly test cases from JSON file."""
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    return [AnomalyCase(**c) for c in data]


def load_incremental_sequences(filepath: Path) -> list[IncrementalSequence]:
    """Load incremental test sequences from JSON file."""
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    return [IncrementalSequence(**s) for s in data]


def load_test_data(test_data_dir: Path) -> TestDataSet:
    """Load and validate all test data from the directory.

    Looks for:
      - standard/standard_samples.json
      - anomaly/anomaly_cases.json
      - incremental/incremental_sequences.json
    """
    test_data_dir = Path(test_data_dir)

    standard_path = test_data_dir / "standard" / "standard_samples.json"
    anomaly_path = test_data_dir / "anomaly" / "anomaly_cases.json"
    incremental_path = test_data_dir / "incremental" / "incremental_sequences.json"

    samples = load_standard_samples(standard_path) if standard_path.exists() else []
    cases = load_anomaly_cases(anomaly_path) if anomaly_path.exists() else []
    sequences = load_incremental_sequences(incremental_path) if incremental_path.exists() else []

    return TestDataSet(
        standard_samples=samples,
        anomaly_cases=cases,
        incremental_sequences=sequences,
    )


def get_dataset_stats(dataset: TestDataSet) -> dict:
    """Return statistics about the test dataset."""
    difficulty_counts = {}
    for s in dataset.standard_samples:
        difficulty_counts[s.difficulty] = difficulty_counts.get(s.difficulty, 0) + 1

    anomaly_type_counts = {}
    for c in dataset.anomaly_cases:
        key = c.anomaly_type.value
        anomaly_type_counts[key] = anomaly_type_counts.get(key, 0) + 1

    return {
        "standard_samples": len(dataset.standard_samples),
        "anomaly_cases": len(dataset.anomaly_cases),
        "incremental_sequences": len(dataset.incremental_sequences),
        "difficulty_distribution": difficulty_counts,
        "anomaly_type_distribution": anomaly_type_counts,
    }
