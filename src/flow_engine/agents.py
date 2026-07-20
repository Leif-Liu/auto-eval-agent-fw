"""Agent specs and runners.

:class:`AgentRunner` drives a real Claude Agent SDK ``query()`` in its own
session. :class:`MockRunner` returns canned / function-computed output without
touching the SDK, so the flow / runtime / checkpoint / resume loop can be
exercised with no API cost.
"""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .types import StepOutput


@dataclass
class AgentSpec:
    """Declarative description of one agent.

    ``mcp_servers`` should be an already-built SDK MCP server dict (see
    ``code_reader_agent._build_options`` for the pattern). ``extra_options`` is
    passed straight through to ``ClaudeAgentOptions``.
    """

    name: str
    system_prompt: str
    model: str | None = None
    max_turns: int = 20
    mcp_servers: dict[str, Any] | None = None
    extra_options: dict[str, Any] = field(default_factory=dict)


# A runner maps (spec, prompt, session_id?) -> StepOutput.
Runner = Callable[..., Awaitable[StepOutput]]


class AgentRunner:
    """Runs an agent step via a fresh Claude Agent SDK query.

    Uses ``query()`` (stateless, one-shot) — the right default for step-level
    HITL flows. Upgrade to ``ClaudeSDKClient`` only if a specific step needs
    mid-turn interrupts or follow-ups within the step.
    """

    async def run(
        self,
        spec: AgentSpec,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> StepOutput:
        from claude_agent_sdk import (  # lazy import: keeps mock mode SDK-free
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        options = ClaudeAgentOptions(
            model=spec.model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            system_prompt=spec.system_prompt,
            permission_mode="bypassPermissions",
            max_turns=spec.max_turns,
            **({"mcp_servers": spec.mcp_servers} if spec.mcp_servers else {}),
            **({"resume": session_id} if session_id else {}),
            **spec.extra_options,
        )

        final, trace = "", []
        async for msg in query(prompt=prompt, options=options):
            kind = type(msg).__name__
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        final = block.text  # keep latest assistant text block
                trace.append({"type": kind, "model": getattr(msg, "model", None)})
            elif isinstance(msg, ResultMessage):
                if msg.result:
                    final = msg.result  # authoritative final text
                trace.append(
                    {
                        "type": kind,
                        "num_turns": msg.num_turns,
                        "is_error": msg.is_error,
                    }
                )
            else:
                trace.append({"type": kind})
        return StepOutput(text=final, trace=trace)


@dataclass
class MockRunner:
    """Offline runner: maps ``(spec, prompt)`` -> text via a callable.

    Use it to validate the flow graph, scheduler, checkpoint and resume path
    without spending API budget.
    """

    fn: Callable[[AgentSpec, str], str]
    delay: float = 0.0

    async def run(
        self,
        spec: AgentSpec,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> StepOutput:
        _ = session_id  # kept for Runner-protocol parity; unused in mock
        if self.delay:
            import asyncio

            await asyncio.sleep(self.delay)
        text = self.fn(spec, prompt)
        return StepOutput(text=text, trace=[{"mock": True, "agent": spec.name}])
