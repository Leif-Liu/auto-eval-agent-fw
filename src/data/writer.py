"""Symmetric write/backup/dedup helpers for the test data flywheel.

``loader.py`` is read-only by design; this module is its write-side counterpart.
Writes are atomic (tmp + ``replace``) and a timestamped backup is taken before
overwriting the golden ``standard_samples.json``.
"""

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.models.dataset_draft import DuplicateHit
from src.models.test_data import StandardSample


def _tokenize(text: str) -> set[str]:
    """Lowercase, strip punctuation, split on whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return set(text.split())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def find_duplicates(
    candidates: list[StandardSample],
    existing: list[StandardSample],
    threshold: float,
) -> list[DuplicateHit]:
    """Detect near-duplicates between candidates and existing samples.

    Uses token-Jaccard similarity over normalized ``input_description``.
    Returns one :class:`DuplicateHit` per candidate whose best match against
    ``existing`` is at or above ``threshold``. ``verdict`` is ``"duplicate"``
    for very high similarity (>=0.95) and ``"near"`` otherwise; below-threshold
    candidates are not returned.
    """
    existing_tokens = [(s.sample_id, _tokenize(s.input_description)) for s in existing]
    hits: list[DuplicateHit] = []
    for i, cand in enumerate(candidates):
        cand_tokens = _tokenize(cand.input_description)
        best_id, best_sim = "", 0.0
        for ex_id, ex_tokens in existing_tokens:
            sim = _jaccard(cand_tokens, ex_tokens)
            if sim > best_sim:
                best_sim, best_id = sim, ex_id
        if best_sim >= threshold:
            verdict = "duplicate" if best_sim >= 0.95 else "near"
            hits.append(
                DuplicateHit(
                    candidate_idx=i,
                    existing_sample_id=best_id,
                    similarity=round(best_sim, 3),
                    verdict=verdict,
                )
            )
    return hits


def backup_standard_file(target: Path, backup_dir: Path) -> Optional[Path]:
    """Copy ``target`` to ``backup_dir/<stem>.<timestamp>.json``. No-op if missing."""
    target = Path(target)
    if not target.exists():
        return None
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{target.stem}.{ts}.json"
    shutil.copy2(target, backup_path)
    return backup_path


def write_standard_samples(samples: list[StandardSample], target: Path) -> Path:
    """Atomically write ``samples`` to ``target`` as JSON.

    The caller is responsible for merging existing + new and for version
    tagging (see :func:`tag_with_version`). Uses ``model_dump(mode="json")``
    so enums serialize as their string values.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = [s.model_dump(mode="json") for s in samples]
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    tmp.replace(target)
    return target


def tag_with_version(sample: StandardSample, version: str) -> StandardSample:
    """Return a copy of ``sample`` with ``metadata.added_in_version`` set."""
    meta = dict(sample.metadata)
    meta["added_in_version"] = version
    return sample.model_copy(update={"metadata": meta})


def merge_samples(
    existing: list[StandardSample], new_samples: list[StandardSample]
) -> list[StandardSample]:
    """Concatenate ``existing`` + ``new_samples`` (existing first, order preserved)."""
    return list(existing) + list(new_samples)


def append_changelog(
    changelog_path: Path,
    version: str,
    added: int,
    note: str,
    backup_file: Optional[Path] = None,
) -> Path:
    """Prepend a version section to CHANGELOG.md (newest first; created if absent)."""
    changelog_path = Path(changelog_path)
    changelog_path.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    header = (
        changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else ""
    )
    lines = [f"## {version} ({today})", f"- +{added} standard samples"]
    if backup_file:
        lines.append(f"- backup: {Path(backup_file).name}")
    if note:
        lines.append(f"- {note}")
    block = "\n".join(lines) + "\n"
    changelog_path.write_text(
        block + ("\n" + header if header else ""), encoding="utf-8"
    )
    return changelog_path
