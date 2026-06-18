# Changelog

## 0.1.0 (2026-06-18)
- Dataset flywheel tooling: turn raw production cases into annotated
  `StandardSample` entries (LLM-assisted draft → expert refine → dedup →
  validate → versioned write-back into the golden test set).
- New CLI group `dataset`: `import`, `stats`, `dedup-check`.
- `StandardSample.difficulty` is now a `DifficultyLevel` StrEnum
  (backward compatible — existing golden set still loads).
- `arch.md`: documented the closed-loop "Agent/LLM evaluates Agent"
  architecture and the data flywheel.
- Golden test set unchanged (n=5); the import path has been verified
  end-to-end via `dataset import --dry-run --auto-accept`.
