"""Scheduler.

Topo-walks the flow, resolves each step's inputs from completed outputs,
runs the step (agent or HITL), and checkpoints after every step. HITL
(human/gate) steps either consume a pending decision (resume case) or raise
:class:`PendingApproval` to pause the run.
"""
from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from .agents import AgentSpec, Runner
from .types import (
    Checkpoint,
    Flow,
    PendingApproval,
    Step,
    StepOutput,
)


def _deps(step: Step, idset: set[str]) -> set[str]:
    """All upstream step ids this step depends on (explicit + input refs).

    Input refs to ids *not* in the flow (e.g. a pre-seeded ``__seed__``) are
    intentionally ignored here — they are data dependencies, not ordering ones.
    """
    deps = set(step.depends_on)
    for ref in step.inputs.values():
        upstream = ref.split(".", 1)[0]
        if upstream in idset and upstream != step.id:
            deps.add(upstream)
    return deps


def topo_order(flow: Flow) -> list[str]:
    """DFS topological order over step ids; raises on cycles."""
    by_id = {s.id: s for s in flow.steps}
    idset = set(by_id)
    order: list[str] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(sid: str) -> None:
        if sid in seen:
            return
        if sid in visiting:
            raise ValueError(f"cycle detected at step {sid!r}")
        visiting.add(sid)
        for d in _deps(by_id[sid], idset):
            visit(d)
        visiting.discard(sid)
        seen.add(sid)
        order.append(sid)

    for s in flow.steps:
        visit(s.id)
    return order


def resolve_inputs(
    inputs: dict[str, str], completed: dict[str, StepOutput]
) -> dict[str, Any]:
    """Resolve ``"upstream.field"`` refs against completed outputs -> local dict.

    Field defaults to ``output`` (the step's text). Also supports ``data``,
    ``trace``, or any key inside ``StepOutput.data``.
    """
    ctx: dict[str, Any] = {}
    for key, ref in inputs.items():
        sid, _, field = ref.partition(".")
        field = field or "output"
        if sid not in completed:
            raise RuntimeError(
                f"input {ref!r} for key {key!r} is not available yet"
            )
        out = completed[sid]
        if field in ("output", "text"):
            ctx[key] = out.text
        elif field == "data":
            ctx[key] = out.data
        elif field == "trace":
            ctx[key] = out.trace
        else:
            ctx[key] = out.data.get(field)
    return ctx


def render_prompt(step: Step, ctx: dict[str, Any]) -> str:
    """Render the step's prompt; falls back to dumping the context as JSON."""
    if step.prompt_template:
        try:
            return step.prompt_template.format(**ctx)
        except KeyError as exc:
            raise RuntimeError(
                f"step {step.id!r} prompt template missing input {exc}"
            ) from exc
    return f"# Context\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n\n# Task\nProceed."


@runtime_checkable
class HitlHandler(Protocol):
    """Resolves a human/gate step into a decision string.

    Implementations decide *how* the decision is collected:

      - ``InteractiveHitl`` : terminal ``input()`` right now (real-time, in
                              process)
      - ``PauseResumeHitl`` : raise PendingApproval, exit, resume later
                              (asynchronous / out-of-band)
      - custom (e.g. Web)   : push to a queue, await a callback

    Whatever a handler returns is recorded as the step's output. Every prior
    step is already checkpointed, so a crash during a HITL step still resumes
    cleanly up to (and re-asking) that step.
    """

    async def __call__(
        self, *, step: Step, prompt: str, context: dict[str, Any], ckpt: Checkpoint
    ) -> str: ...


class PauseResumeHitl:
    """Pause by raising :class:`PendingApproval` (exit, then resume later).

    The right choice for services / async deployments where the human decides
    out of band, or for batch runs where no terminal is attached.
    """

    async def __call__(self, *, step, prompt, context, ckpt) -> str:  # type: ignore[no-untyped-def]
        raise PendingApproval(step_id=step.id, prompt=prompt, context=context)


class InteractiveHitl:
    """Real-time: block on terminal ``input()`` for an immediate decision.

    The "real-time" half of real-time pause-resume — the run stays in process
    and waits for you to type, but every step is still checkpointed, so if the
    process is killed mid-wait the next run resumes up to this step and asks
    again.
    """

    def __init__(self, prompt_prefix: str = "HITL") -> None:
        self.prompt_prefix = prompt_prefix

    async def __call__(self, *, step, prompt, context, ckpt) -> str:  # type: ignore[no-untyped-def]
        import asyncio

        print(f"\n[{self.prompt_prefix}] step {step.id!r}: {prompt}")
        preview = json.dumps(context, ensure_ascii=False)[:200]
        print(f"  context: {preview}…")
        # input() blocks the OS thread; run it off the event loop so the loop
        # stays responsive (matters once concurrent agent steps are added).
        raw = await asyncio.to_thread(input, "  your decision> ")
        return raw.strip()


async def execute(
    flow: Flow,
    *,
    ckpt: Checkpoint,
    runner: Runner,
    hitl: HitlHandler | None = None,
    skill_registry: dict[str, AgentSpec] | None = None,
) -> dict[str, StepOutput]:
    """Walk the flow in topo order, running each not-yet-completed step.

    HITL (human/gate) steps are resolved in priority order:

      1. if a decision is already in ``ckpt.pending`` (injected via ``--resume``
         or an external system), consume it — the resume path;
      2. otherwise call ``hitl`` (default :class:`PauseResumeHitl`, which raises
         :class:`PendingApproval` so the caller can persist + collect a decision
         out of band; pass :class:`InteractiveHitl` for real-time terminal
         input).

    Every prior step is already checkpointed, so a crash at any point resumes
    cleanly. Returns the full ``completed`` map when the flow finishes.
    """
    order = topo_order(flow)
    for sid in order:
        if sid in ckpt.completed:
            continue  # resume: skip already-completed steps
        step = flow.step(sid)
        ctx = resolve_inputs(step.inputs, ckpt.completed)

        if step.kind in ("human", "gate"):
            if sid in ckpt.pending:  # decision injected via --resume / external
                text = ckpt.pending.pop(sid)
            else:
                prompt = (
                    step.human.prompt
                    if step.human and step.human.prompt
                    else f"Approve step {sid!r}?"
                )
                handler = hitl if hitl is not None else PauseResumeHitl()
                text = await handler(step=step, prompt=prompt, context=ctx, ckpt=ckpt)
            out = StepOutput(text=text, trace=[{"human_input": True}])
        else:
            spec = step.agent or (skill_registry or {}).get(step.skill or "")
            if spec is None:
                hint = (
                    f" and skill {step.skill!r} not in registry" if step.skill else ""
                )
                raise RuntimeError(f"step {sid!r} has no agent spec{hint}")
            prompt = render_prompt(step, ctx)
            out = await runner.run(spec, prompt, session_id=step.session_id)

        ckpt.completed[sid] = out
        ckpt.save()
    return ckpt.completed
