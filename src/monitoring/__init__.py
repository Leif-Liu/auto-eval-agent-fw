"""Evolution dashboard / monitoring agent.

Quantifies the evaluated agent's capability growth across iterations by
aggregating archived ``results/*/report.json`` runs into:

- run-over-run score deltas
- the L1 -> L4 maturity trajectory
- per-dimension improvement slopes (linear regression)
- an optional LLM-generated Chinese evolution narrative

The dashboard is read-only and offline-capable; only the insight narrative
requires the vLLM judge endpoint.
"""
