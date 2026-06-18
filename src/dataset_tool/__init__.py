"""Dataset flywheel tooling: import, annotate (LLM-assisted), validate, commit.

This subpackage is a side-channel to the evaluation pipeline. It turns raw
production cases into annotated :class:`~src.models.test_data.StandardSample`
entries, writes them back into the golden test set with version tags, and lets
the user re-run evaluation against a previous baseline via the existing
``run_full_evaluation(baseline_path=...)`` API.
"""
