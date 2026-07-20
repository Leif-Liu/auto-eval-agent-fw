"""Core data structures for the flow runtime.

A :class:`Flow` is a DAG of :class:`Step` nodes. Each step is one of:

  - ``agent``  : runs a Claude Agent SDK query in its own session
  - ``skill``  : like agent, but resolved through a skill registry (a fixed
                 agent template)
  - ``human``  : a human produces structured input (an "agent" step for people)
  - ``gate``   : an approval gate (human decides go / modify / stop)

State flows between steps explicitly via context references
(``"upstream_step.output"``), not through CLI-internal context.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .agents import AgentSpec

StepKind = Literal["agent", "skill", "human", "gate"]


@dataclass
class StepOutput:
    """Result of executing one step."""

    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class HumanSpec:
    """Configuration for a human / gate step.

    ``prompt`` is shown to the human; ``output_hint`` (optional) describes the
    expected shape of their decision.
    """

    prompt: str = ""
    output_hint: str | None = None


class PendingApproval(Exception):
    """Signal raised by the runtime when a HITL step needs a human decision.

    The caller (CLI / service) catches this, persists the checkpoint, collects
    the human decision, then resumes the run with that decision injected into
    ``Checkpoint.pending``.

    Raised as an exception (not returned) so the pause propagates out of
    ``execute()`` from any depth without special-case return plumbing.
    """

    def __init__(self, step_id: str, prompt: str, context: dict[str, Any]) -> None:
        super().__init__(step_id)
        self.step_id = step_id
        self.prompt = prompt
        self.context = context


@dataclass
class Step:
    id: str
    kind: StepKind = "agent"
    # Only one of the following is consulted, depending on `kind`:
    agent: AgentSpec | None = None
    skill: str | None = None  # skill name, resolved via a registry at runtime
    human: HumanSpec | None = None
    # inputs: local-name -> "<upstream_step_id>.<field>"
    # field defaults to "output" (i.e. StepOutput.text); also: "data", "trace".
    inputs: dict[str, str] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    # Optional: render the agent prompt from upstream outputs. If absent, the
    # runtime passes the resolved context as JSON.
    prompt_template: str | None = None
    # Optional: reuse an SDK session id so an agent keeps multi-turn memory
    # across steps; otherwise each step is stateless.
    session_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind in ("agent", "skill") and not (self.agent or self.skill):
            raise ValueError(
                f"step {self.id!r} (kind={self.kind}) needs `agent` or `skill`"
            )
        if self.kind in ("human", "gate") and self.human is None:
            self.human = HumanSpec()  # sensible default
        for ref in self.inputs.values():
            upstream = ref.split(".", 1)[0]
            if upstream == self.id:
                raise ValueError(f"step {self.id!r} cannot reference its own output")


@dataclass
class Flow:
    steps: list[Step]

    def __post_init__(self) -> None:
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate step ids")
        idset = set(ids)
        for s in self.steps:
            for d in s.depends_on:
                if d not in idset:
                    raise ValueError(f"step {s.id!r} depends on unknown step {d!r}")

    def step(self, sid: str) -> Step:
        return next(s for s in self.steps if s.id == sid)


@dataclass
class Checkpoint:
    """Persisted run state: completed steps + pending human decisions.

    Saved atomically after every step. ``pending`` holds human decisions fed in
    at resume time; the runtime consumes (pops) an entry as it passes that step.
    """

    run_id: str
    flow_id: str
    completed: dict[str, StepOutput] = field(default_factory=dict)
    pending: dict[str, str] = field(default_factory=dict)
    _path: str | None = field(default=None, repr=False)

    def save(self) -> None:
        if not self._path:
            return
        payload = {
            "run_id": self.run_id,
            "flow_id": self.flow_id,
            "completed": {k: asdict(v) for k, v in self.completed.items()},
            "pending": dict(self.pending),
        }
        tmp = f"{self._path}.tmp"
        Path(tmp).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        os.replace(tmp, self._path)  # atomic on POSIX

    @classmethod
    def load(cls, path: str) -> Checkpoint:
        data = json.loads(Path(path).read_text())
        ckpt = cls(
            run_id=data["run_id"],
            flow_id=data["flow_id"],
            pending=dict(data.get("pending", {})),
            _path=path,
        )
        ckpt.completed = {
            sid: StepOutput(**v) for sid, v in data.get("completed", {}).items()
        }
        return ckpt

    @classmethod
    def new(cls, run_id: str, flow_id: str, path: str | None = None) -> Checkpoint:
        return cls(run_id=run_id, flow_id=flow_id, _path=path)
