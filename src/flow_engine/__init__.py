"""Multi-agent flow runtime on top of the Claude Agent SDK.

Pipeline::

    Flow (DAG of Steps)
      -> execute()        [topo walk + context bus + checkpoint + HITL]
        -> AgentRunner    (one SDK query per agent step)
          -> Claude Agent SDK
            -> Claude Code CLI

Each agent step runs in its own SDK session; state between steps is passed
explicitly via the context bus (refs like ``other_step.output``), not via
CLI-internal context. Every completed step is checkpointed, so a run can
resume after a crash or after a HITL pause.

See ``example_flow.py`` for a runnable end-to-end demo (mock + real + resume).
"""
from .agents import AgentRunner, AgentSpec, MockRunner
from .runtime import (
    HitlHandler,
    InteractiveHitl,
    PauseResumeHitl,
    execute,
    render_prompt,
    resolve_inputs,
    topo_order,
)
from .types import (
    Checkpoint,
    Flow,
    HumanSpec,
    PendingApproval,
    Step,
    StepKind,
    StepOutput,
)

__all__ = [
    "AgentRunner",
    "AgentSpec",
    "Checkpoint",
    "Flow",
    "HitlHandler",
    "InteractiveHitl",
    "HumanSpec",
    "MockRunner",
    "PauseResumeHitl",
    "PendingApproval",
    "Step",
    "StepKind",
    "StepOutput",
    "execute",
    "render_prompt",
    "resolve_inputs",
    "topo_order",
]
