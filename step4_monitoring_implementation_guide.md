# STEP 4 | Monitoring & Ops — Implementation Guide

Defect Description Agent Evaluation Framework v3.0

This document details the concrete implementation approach, data sources, code examples, and alert rules for each of the 6 monitoring items defined in STEP 4.

## Overall Monitoring Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                   STEP 4 Monitoring Stack                    │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────┐│
│  │ Agent Server │  │  Frontend   │  │   LLM-as-a-Judge     ││
│  │  Run Logs    │  │  Event Logs │  │   Daily Cron         ││
│  └──────┬──────┘  └──────┬──────┘  └──────────┬───────────┘│
│         │                │                     │             │
│         ▼                ▼                     ▼             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │          Log Aggregation (ELK / Datadog / Splunk)    │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                    │
│  ┌──────────────────────▼───────────────────────────────┐   │
│  │              Metrics Calculation Engine               │   │
│  │  ┌────────┬────────┬────────┬────────┬──────┬──────┐ │   │
│  │  │1.Avail │2.Effic │3.Qual  │4.Feedbk│5.Anom│6.Drft│ │   │
│  │  │Success%│Redund% │AvgScr  │Adopt%  │Hndl% │Drift │ │   │
│  │  └────────┴────────┴────────┴────────┴──────┴──────┘ │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                    │
│  ┌──────────────────────▼───────────────────────────────┐   │
│  │         Alert Rules & Dashboard (Grafana)            │   │
│  │  Threshold breach → Email / Slack / PagerDuty alert  │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

---

## 1. Availability — Success Rate & Timeout Monitoring

**Alert Rule:** Success Rate < 90% → trigger alert

**Data Source:** Agent server-side run logs (auto-recorded per request via middleware)

**Collected Fields:**

| Field | Description | Example Value |
|-------|-------------|---------------|
| `request_id` | Unique request identifier | `REQ-20260509-0001` |
| `timestamp` | Request time | `2026-05-09T10:32:15Z` |
| `status_code` | Response status | `200` / `500` / `408` |
| `latency_ms` | Response latency | `2350` |
| `error_type` | Error type (if any) | `TimeoutError` / `LLMRateLimitError` |

**Implementation Example:**

```python
# Agent Server — Request Interceptor / Middleware
import time
from datetime import datetime

def agent_request_handler(request):
    start = time.time()
    try:
        result = agent.process(request)
        status = "success"
        error = None
    except TimeoutError:
        status = "timeout"
        error = "TimeoutError"
    except Exception as e:
        status = "failure"
        error = str(type(e).__name__)
    finally:
        latency = (time.time() - start) * 1000
        # Write to monitoring system
        log_metric({
            "request_id": request.id,
            "timestamp": datetime.utcnow().isoformat(),
            "status": status,
            "latency_ms": round(latency, 2),
            "error_type": error
        })
    return result
```

**Alert Calculation:**

```python
# Daily aggregation
daily_success_rate = count(status == "success") / count(all_requests)

if daily_success_rate < 0.90:
    trigger_alert("Availability", f"Success rate {daily_success_rate:.1%} < 90%")

# Latency alert (optional)
if p99_latency > 10000:  # 10 seconds
    trigger_alert("Availability", f"P99 latency {p99_latency}ms > 10s threshold")
```

**Dashboard:** Datadog / Grafana panels showing daily request volume, success rate trend, and latency distribution (P50 / P95 / P99).

---

## 2. Efficiency — Redundant Reasoning Ratio Monitoring

**Alert Rule:** Redundant reasoning ratio > 20% → trigger alert

**Data Source:** Agent reasoning logs (Reasoning Log); requires the Agent framework to record a trace at each reasoning step.

**Collected Fields:**

| Field | Description | Example Value |
|-------|-------------|---------------|
| `session_id` | Session identifier | `SES-00123` |
| `turn_number` | Current conversation turn | `3` (3rd follow-up) |
| `reasoning_steps` | Steps executed in this turn | `["field_extract", "conflict_detect", "summary_gen"]` |
| `reused_steps` | Steps reused from previous turn | `["field_extract"]` |
| `repeated_steps` | Steps unnecessarily re-executed | `["conflict_detect"]` (input unchanged but re-run) |

**Implementation Example:**

```python
class ReasoningTracer:
    """Step-level trace recorder inside the Agent reasoning engine."""

    def __init__(self, session_id: str, turn: int, prev_context: dict = None):
        self.session_id = session_id
        self.turn = turn
        self.prev_context = prev_context or {}
        self.steps_executed = []
        self.steps_reused = []
        self.steps_repeated = []

    def execute_step(self, step_name: str, input_hash: str):
        self.steps_executed.append(step_name)

        prev_hash = self.prev_context.get(step_name, {}).get("input_hash")
        has_cache = self.prev_context.get(step_name, {}).get("result") is not None
        input_changed = (prev_hash != input_hash)

        if not input_changed and has_cache:
            # Input unchanged & cache available → should reuse
            # Check if Agent actually reused or re-computed
            if self._was_recomputed(step_name):
                self.steps_repeated.append(step_name)
            else:
                self.steps_reused.append(step_name)

    def report(self) -> dict:
        total = len(self.steps_executed)
        repeated = len(self.steps_repeated)
        return {
            "session_id": self.session_id,
            "turn": self.turn,
            "total_steps": total,
            "reused_steps": len(self.steps_reused),
            "repeated_steps": repeated,
            "redundant_ratio": repeated / total if total > 0 else 0.0
        }
```

**Alert Calculation:**

```python
# Daily aggregation across all multi-turn requests
daily_redundant_ratio = sum(repeated_steps) / sum(total_steps)

if daily_redundant_ratio > 0.20:
    trigger_alert("Efficiency", f"Redundant reasoning ratio {daily_redundant_ratio:.1%} > 20%")
```

**Worked Example:**

| Turn | User Action | Expected Steps | Actual Steps | Repeated |
|------|-------------|---------------|--------------|----------|
| 1 | Submit full defect description | field_extract → conflict_detect → grammar_check → summary_gen (4) | 4 steps executed | 0 |
| 2 | Modify one field value only | field_extract → summary_gen (2) | 4 steps executed (all re-run) | 2 (conflict_detect, grammar_check) |
| 3 | Ask "did conflict results change?" | Direct answer from cache (0 new) | conflict_detect re-run (1 step) | 1 |

Turn 2 redundant ratio = 2/4 = 50%; Turn 3 redundant ratio = 1/1 = 100%. These would contribute to the daily aggregate.

---

## 3. Quality — Daily Output Quality Sampling

**Alert Rule:** Average score below baseline by 5+ points for 3 consecutive days → trigger alert

**Data Source:** Agent production outputs, scored automatically by LLM-as-a-Judge.

**Implementation Example:**

```python
# Daily Cron Job (e.g., Airflow DAG, scheduled at 01:00 UTC)
import random
from statistics import mean

# LLM-as-a-Judge evaluation prompt template
JUDGE_PROMPT = """
You are an expert evaluator for Defect Description Agent outputs.
Score the following Agent output on a 0-100 scale across these dimensions:

1. Semantic Accuracy (weight 0.35): Does the summary accurately convey
   the core meaning?
2. Field Correctness (weight 0.30): Are structured fields
   (Product Line, VCU, Security Level, etc.) correctly extracted?
3. Summary Quality (weight 0.20): Is the defect description concise
   and accurate?
4. Completeness (weight 0.15): Is any key information missing?
   Is any hallucinated information introduced?

[Original Input]: {input}
[Agent Output]:   {output}

Return a JSON: {{"semantic": X, "field": X, "quality": X,
"completeness": X, "composite": X}}
"""

def daily_quality_sampling():
    # 1. Random sample 10-20 outputs from today's production logs
    today_outputs = get_today_agent_outputs()
    samples = random.sample(today_outputs, min(15, len(today_outputs)))

    scores = []
    for sample in samples:
        # 2. Call judge LLM (temperature=0 for consistency)
        result = llm_judge.evaluate(
            prompt=JUDGE_PROMPT.format(
                input=sample.defect_description,
                output=sample.agent_summary
            ),
            temperature=0
        )
        scores.append(result["composite"])

    daily_avg = mean(scores)

    # 3. Store to time-series database
    store_metric("quality_daily_avg", daily_avg, date=today())

    # 4. Check 3-day consecutive decline
    last_3_days = get_metrics("quality_daily_avg", days=3)
    baseline = get_baseline_score("summary_accuracy")  # e.g., 86

    if len(last_3_days) >= 3 and all(s < baseline - 5 for s in last_3_days):
        trigger_alert(
            "Quality",
            f"3-day avg scores {last_3_days} all below baseline "
            f"({baseline}) by 5+ points"
        )

    return {"date": today(), "samples": len(samples), "avg_score": daily_avg}
```

**Worked Example:**

| Day | Sampled Outputs | Avg Score | Baseline | Delta | Alert? |
|-----|----------------|-----------|----------|-------|--------|
| Day 1 | 15 | 82 | 86 | -4 | No (< 5) |
| Day 2 | 15 | 79 | 86 | -7 | No (only 1 day) |
| Day 3 | 15 | 80 | 86 | -6 | No (only 2 consecutive days) |
| Day 4 | 15 | 78 | 86 | -8 | ⚠️ Yes (3 consecutive days below by 5+) |

**Consistency Safeguards:**

- LLM temperature set to 0 to reduce randomness
- Each sample scored 3 times, take the average
- Calibration set of 20-30 human-scored samples to validate LLM-human correlation ≥ 0.85
- Monthly 10% human spot-check of LLM scores

---

## 4. Feedback — User Adoption Rate Monitoring

**Alert Rule:** Adoption rate < 60% → trigger alert

**Data Source:** Frontend event tracking (user click/action events on Agent output).

**Event Definitions:**

| Event | Description | Classification |
|-------|-------------|----------------|
| `accept` | User directly adopts Agent output without modification | ✅ Adopted |
| `edit_then_accept` | User modifies the output then accepts | 📝 Edited (partial adoption) |
| `reject` | User rejects Agent output and manually rewrites | ❌ Rejected |
| `ignore` | User takes no action on Agent output for > N minutes | ❌ Ignored |

**Implementation Example — Frontend:**

```javascript
// Frontend — User action event tracking
function trackAgentOutput(sessionId, outputId, agentOutput) {

    // "Accept" button click
    document.getElementById("accept-btn").addEventListener("click", () => {
        analytics.track("agent_output_action", {
            session_id: sessionId,
            output_id: outputId,
            action: "accept",
            timestamp: new Date().toISOString()
        });
    });

    // "Submit" after editing
    document.getElementById("submit-btn").addEventListener("click", () => {
        const userFinalText = document.getElementById("editor").value;
        const editDistance = levenshteinDistance(agentOutput, userFinalText);
        const editRatio = editDistance / agentOutput.length;

        analytics.track("agent_output_action", {
            session_id: sessionId,
            output_id: outputId,
            action: editRatio > 0 ? "edit_then_accept" : "accept",
            edit_distance: editDistance,
            edit_ratio: editRatio.toFixed(3),
            original_text: agentOutput,
            modified_text: userFinalText,
            timestamp: new Date().toISOString()
        });
    });

    // "Reject / Regenerate" button click
    document.getElementById("reject-btn").addEventListener("click", () => {
        analytics.track("agent_output_action", {
            session_id: sessionId,
            output_id: outputId,
            action: "reject",
            timestamp: new Date().toISOString()
        });
    });

    // Ignore detection — no action after 10 minutes
    setTimeout(() => {
        if (!userHasActed(outputId)) {
            analytics.track("agent_output_action", {
                session_id: sessionId,
                output_id: outputId,
                action: "ignore",
                timestamp: new Date().toISOString()
            });
        }
    }, 10 * 60 * 1000);
}
```

**Implementation Example — Backend Aggregation:**

```python
def daily_feedback_report():
    events = get_today_events("agent_output_action")

    accept_count = count(e for e in events if e.action == "accept")
    edit_count   = count(e for e in events if e.action == "edit_then_accept")
    reject_count = count(e for e in events if e.action == "reject")
    ignore_count = count(e for e in events if e.action == "ignore")
    total = len(events)

    adoption_rate = (accept_count + edit_count) / total if total > 0 else 1.0
    edit_rate     = edit_count / total if total > 0 else 0.0
    reject_rate   = reject_count / total if total > 0 else 0.0

    store_metric("adoption_rate", adoption_rate, date=today())

    if adoption_rate < 0.60:
        trigger_alert(
            "Feedback",
            f"Adoption rate {adoption_rate:.0%} < 60%. "
            f"Accept: {accept_count}, Edit: {edit_count}, "
            f"Reject: {reject_count}, Ignore: {ignore_count}"
        )

    return {
        "date": today(),
        "total": total,
        "adoption_rate": adoption_rate,
        "edit_rate": edit_rate,
        "reject_rate": reject_rate
    }
```

**Worked Example:**

| Day | Accept | Edit | Reject | Ignore | Total | Adoption Rate | Alert? |
|-----|--------|------|--------|--------|-------|---------------|--------|
| Mon | 50 | 15 | 25 | 10 | 100 | 65% | No |
| Tue | 40 | 12 | 35 | 13 | 100 | 52% | ⚠️ Yes |
| Wed | 45 | 12 | 30 | 13 | 100 | 57% | ⚠️ Yes |

---

## 5. Anomaly — Exception Handling Success Rate

**Alert Rule:** Exception handling success rate < 90% → trigger alert

**Data Source:** Agent run logs, focusing on abnormal input scenarios and their processing results.

**Exception Input Types:**

| Code | Type | Typical Input |
|------|------|---------------|
| E1 | Oversized Input | Defect description exceeding 10,000 characters |
| E2 | Gibberish | Random character strings, special symbol sequences |
| E3 | Format Error | Missing required fields, malformed JSON |
| E4 | Mixed Language | Heavily mixed Chinese-English input |

**Pass/Fail Criteria:**

| Result | Criteria | Example |
|--------|----------|---------|
| ✅ Pass — Graceful Degradation | Returns partial valid results with explanation | "Warning: product_line field missing, results may be incomplete." |
| ✅ Pass — Clear Error Message | Returns meaningful error guiding user to fix input | "Please provide a valid defect description." |
| ❌ Fail — Meaningless Output | Returns gibberish, blank, or completely unrelated content | `null` / empty string / random tokens |
| ❌ Fail — Silent Swallow | No error, no prompt, user cannot perceive the anomaly | Appears to succeed but output is nonsensical |

**Implementation Example:**

```python
import math
from collections import Counter

def classify_input(defect_description: str) -> str:
    """Auto-classify input as normal or one of E1-E4 exception types."""
    if not defect_description or defect_description.strip() == "":
        return "E3_format_error"
    if len(defect_description) > 10000:
        return "E1_oversized"
    if is_gibberish(defect_description):
        return "E2_gibberish"
    if not validate_required_fields(defect_description):
        return "E3_format_error"
    if mixed_language_ratio(defect_description) > 0.4:
        return "E4_mixed_language"
    return "normal"


def is_gibberish(text: str) -> bool:
    """Detect gibberish using character entropy and alpha ratio."""
    if len(text) < 5:
        return True
    # Character-level entropy
    freq = Counter(text)
    length = len(text)
    entropy = -sum((c / length) * math.log2(c / length) for c in freq.values())
    # High entropy + low alphabetic ratio = likely gibberish
    alpha_ratio = sum(1 for c in text if c.isalpha()) / length
    return entropy > 5.5 and alpha_ratio < 0.3


def evaluate_anomaly_handling(input_type: str, agent_response) -> str:
    """Judge whether Agent correctly handled an abnormal input."""
    # Fail: no response or empty
    if agent_response is None or str(agent_response).strip() == "":
        return "fail_silent_swallow"

    response_text = str(agent_response)

    # Fail: response is itself gibberish
    if is_gibberish(response_text):
        return "fail_meaningless_output"

    # Pass: contains explicit error message or graceful fallback
    error_indicators = [
        "error", "invalid", "unable to", "please provide",
        "cannot process", "exceeds", "missing", "warning"
    ]
    if any(indicator in response_text.lower() for indicator in error_indicators):
        return "pass_clear_error"

    # Pass: returns partial but meaningful result
    if len(response_text) > 20 and not is_gibberish(response_text):
        return "pass_graceful_degradation"

    return "fail_meaningless_output"


def daily_anomaly_report():
    """Daily aggregation of anomaly handling performance."""
    all_requests = get_today_requests()
    anomaly_requests = [r for r in all_requests if classify_input(r.input) != "normal"]

    results = []
    for r in anomaly_requests:
        verdict = evaluate_anomaly_handling(
            classify_input(r.input), r.agent_response
        )
        results.append({
            "request_id": r.id,
            "input_type": classify_input(r.input),
            "verdict": verdict
        })

    passed = sum(1 for r in results if r["verdict"].startswith("pass"))
    total = len(results)
    success_rate = passed / total if total > 0 else 1.0

    store_metric("anomaly_handling_rate", success_rate, date=today())

    if success_rate < 0.90:
        failed_cases = [r for r in results if not r["verdict"].startswith("pass")]
        trigger_alert(
            "Anomaly",
            f"Handling rate {success_rate:.0%} < 90%. "
            f"Failed cases: {len(failed_cases)}/{total}"
        )

    return {"date": today(), "total": total, "passed": passed, "rate": success_rate}
```

**Worked Example:**

| Input | Type | Agent Response | Verdict |
|-------|------|---------------|---------|
| 12,000-char description | E1 Oversized | "Input exceeds maximum length (10,000 chars). Please shorten your description." | ✅ Pass |
| `@#$%^&*!!!` | E2 Gibberish | "Unable to parse input. Please provide a valid defect description." | ✅ Pass |
| JSON missing `product_line` | E3 Format | Returns partial Summary + "Warning: product_line field missing." | ✅ Pass (Graceful) |
| 70% Chinese / 30% English mixed | E4 Mixed Lang | Agent crashes, returns empty response | ❌ Fail (Silent) |

Daily result: 3 passed / 4 total = 75% → ⚠️ Alert triggered (< 90%).

---

## 6. Drift Detection — Data, Model & Prompt Drift

**Alert Rule:** Drift detected → change-triggered re-run of Golden Test Set

**Three Types of Drift:**

| Drift Type | What It Detects | Trigger | Detection Cadence |
|------------|----------------|---------|-------------------|
| Data Drift | Production input distribution diverges from training/test data | Continuous (daily) | Daily auto-sampling |
| Model Drift | LLM base model update changes output style or quality | On model change | Change-triggered |
| Prompt Drift | Accumulated side-effects from multiple prompt modifications | On prompt change | Change-triggered |

### 6.1 Data Drift — Input Distribution Shift

**Core Idea:** Compare statistical features of production inputs against the Golden Test Set baseline distribution.

**Implementation Example:**

```python
import numpy as np
from scipy.stats import entropy as kl_divergence
from sentence_transformers import SentenceTransformer

class DataDriftDetector:
    """Detect distribution shift between production inputs
    and the Golden Test Set baseline."""

    def __init__(self, golden_test_set: list):
        self.encoder = SentenceTransformer("all-MiniLM-L6-v2")
        # Pre-compute baseline feature distributions
        self.baseline_stats = self._compute_stats(golden_test_set)
        self.baseline_embeddings = self.encoder.encode(golden_test_set)

    def _compute_stats(self, texts: list) -> dict:
        """Compute statistical features of a text corpus."""
        lengths = [len(t) for t in texts]
        field_missing = [self._count_missing_fields(t) for t in texts]
        lang_ratios = [self._chinese_ratio(t) for t in texts]
        return {
            "avg_length": np.mean(lengths),
            "std_length": np.std(lengths),
            "length_distribution": np.histogram(lengths, bins=20)[0],
            "avg_field_missing": np.mean(field_missing),
            "avg_chinese_ratio": np.mean(lang_ratios),
        }

    def detect(self, production_inputs: list) -> dict:
        """Run drift detection on a batch of production inputs."""
        prod_stats = self._compute_stats(production_inputs)

        # 1. Text-level statistical comparison
        length_shift = abs(
            prod_stats["avg_length"] - self.baseline_stats["avg_length"]
        ) / self.baseline_stats["std_length"]

        # 2. Embedding-space distribution comparison (MMD)
        prod_embeddings = self.encoder.encode(production_inputs)
        mmd_score = self._compute_mmd(
            self.baseline_embeddings, prod_embeddings
        )

        # 3. KL Divergence on length distribution
        baseline_dist = self.baseline_stats["length_distribution"] + 1e-10
        prod_dist = np.histogram(
            [len(t) for t in production_inputs], bins=20
        )[0] + 1e-10
        kl_score = kl_divergence(
            prod_dist / prod_dist.sum(),
            baseline_dist / baseline_dist.sum()
        )

        is_drifted = (
            length_shift > 2.0 or   # > 2 std deviations
            mmd_score > 0.05 or     # MMD threshold
            kl_score > 0.5          # KL divergence threshold
        )

        return {
            "date": today(),
            "length_shift_sigma": round(length_shift, 2),
            "mmd_score": round(mmd_score, 4),
            "kl_divergence": round(kl_score, 4),
            "is_drifted": is_drifted
        }

    def _compute_mmd(self, X, Y):
        """Maximum Mean Discrepancy between two embedding sets."""
        XX = np.mean(np.dot(X, X.T))
        YY = np.mean(np.dot(Y, Y.T))
        XY = np.mean(np.dot(X, Y.T))
        return XX + YY - 2 * XY


# Daily Cron Job
def daily_data_drift_check():
    detector = DataDriftDetector(load_golden_test_set())
    today_inputs = get_today_production_inputs()
    result = detector.detect(today_inputs)

    store_metric("data_drift", result, date=today())

    if result["is_drifted"]:
        trigger_alert(
            "Drift-Data",
            f"Data drift detected: MMD={result['mmd_score']}, "
            f"KL={result['kl_divergence']}, "
            f"Length shift={result['length_shift_sigma']}σ"
        )
```

### 6.2 Model Drift — Post-Update Quality Regression

**Core Idea:** Automatically re-run the full Golden Test Set whenever the LLM base model is updated, and compare scores against the previous baseline.

**Implementation Example:**

```python
# CI/CD Pipeline — triggered on model version change
def model_drift_check(new_model_version: str):
    """Re-run Golden Test Set after model update,
    compare with stored baseline."""

    golden_set = load_golden_test_set()
    previous_baseline = load_baseline_scores()

    # Run full evaluation with new model
    new_scores = run_full_evaluation(golden_set, model_version=new_model_version)

    # Compare each dimension
    drift_detected = False
    report = []
    for dimension in ["summary", "conflict", "grammar", "output", "stability"]:
        old = previous_baseline[dimension]
        new = new_scores[dimension]
        delta = new - old
        is_regression = delta < -3  # threshold: drop > 3 points

        report.append({
            "dimension": dimension,
            "old_score": old,
            "new_score": new,
            "delta": delta,
            "regression": is_regression
        })

        if is_regression:
            drift_detected = True

    if drift_detected:
        trigger_alert(
            "Drift-Model",
            f"Model drift detected after update to {new_model_version}. "
            f"Regressions: {[r for r in report if r['regression']]}"
        )
        # Block deployment until reviewed
        block_deployment(new_model_version, reason="model_drift")
    else:
        # Update baseline with new scores
        update_baseline(new_scores, model_version=new_model_version)

    return report
```

**CI/CD Integration:**

```yaml
# .github/workflows/model_update.yml (example)
name: Model Update Drift Check
on:
  push:
    paths:
      - 'config/model_version.yaml'

jobs:
  drift-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run Golden Test Set
        run: python scripts/model_drift_check.py
      - name: Compare Scores
        run: python scripts/compare_baseline.py
      - name: Block if Regression
        if: failure()
        run: echo "Model drift detected. Deployment blocked."
```

### 6.3 Prompt Drift — Accumulated Prompt Degradation

**Core Idea:** Version-control all prompts; on every prompt change, auto-run regression tests on the affected subset and compare with previous scores.

**Implementation Example:**

```python
# CI/CD Pipeline — triggered on prompt file change
def prompt_drift_check(changed_prompt_file: str, prompt_version: str):
    """Run regression test on affected test subset
    after a prompt change."""

    # 1. Identify which evaluation dimensions are affected
    prompt_to_dimension = {
        "prompts/summary_gen.txt": "summary",
        "prompts/conflict_detect.txt": "conflict",
        "prompts/grammar_check.txt": "grammar",
    }
    affected_dim = prompt_to_dimension.get(changed_prompt_file, "all")

    # 2. Load relevant test subset
    if affected_dim == "all":
        test_subset = load_golden_test_set()
    else:
        test_subset = load_golden_test_subset(dimension=affected_dim)

    # 3. Run evaluation with new prompt
    previous_scores = load_dimension_scores(affected_dim)
    new_scores = run_dimension_evaluation(test_subset, affected_dim)

    # 4. Compare
    delta = new_scores["avg"] - previous_scores["avg"]
    is_regression = delta < -2  # tighter threshold for prompt changes

    if is_regression:
        trigger_alert(
            "Drift-Prompt",
            f"Prompt drift in {changed_prompt_file} (v{prompt_version}): "
            f"{affected_dim} score {previous_scores['avg']:.1f} → "
            f"{new_scores['avg']:.1f} (Δ{delta:+.1f})"
        )
        # Block merge
        block_merge(
            pr_id=get_current_pr(),
            reason=f"Prompt regression: {affected_dim} Δ{delta:+.1f}"
        )
    else:
        approve_merge(get_current_pr())

    return {
        "prompt_file": changed_prompt_file,
        "version": prompt_version,
        "affected_dimension": affected_dim,
        "old_score": previous_scores["avg"],
        "new_score": new_scores["avg"],
        "delta": delta,
        "regression": is_regression
    }
```

**Detection Cadence Summary:**

| Cadence | Action | Automation |
|---------|--------|------------|
| Daily | Sample production inputs → Data drift statistical check | ✅ Cron job |
| Weekly | Aggregate daily drift metrics → Trend summary report | ✅ Scheduled report |
| On model change | Re-run full Golden Test Set → Compare all 5 dimensions | ✅ CI/CD triggered |
| On prompt change | Re-run affected test subset → Compare dimension score | ✅ CI/CD triggered |
| Quarterly | Full review of Golden Test Set distribution vs production | 🔄 Semi-auto (expert review) |

---

## Summary

| # | Monitor Item | Data Source | Implementation | Automation |
|---|-------------|-------------|----------------|------------|
| 1 | Availability | Agent server logs (middleware) | Request interceptor logs status + latency per call | ✅ Fully automated |
| 2 | Efficiency | Reasoning trace logs | Step-level tracer compares reused vs repeated steps | ✅ Fully automated |
| 3 | Quality | Production outputs + LLM-as-a-Judge | Daily cron samples 10–20 outputs → LLM scores → trend check | ✅ Fully automated |
| 4 | Feedback | Frontend event tracking | User click events (accept/edit/reject) → compute adoption rate | ✅ Fully automated |
| 5 | Anomaly | Agent logs + input classifier | Auto-classify input type → evaluate response quality | ✅ Fully automated |
| 6 | Drift Detection | Input distributions + CI/CD triggers | Daily statistical comparison + change-triggered regression test | ✅ Fully automated |

All 6 monitoring items can be fully automated. The only manual intervention required is post-alert root-cause analysis and fix decisions, which fall under STEP 5–6 (Iterative Optimization).
