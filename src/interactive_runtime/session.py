"""InteractiveSession — a ClaudeSDKClient-driven agent step with tool-level HITL.

Unlike ``flow_engine``'s ``AgentRunner`` (which uses ``query()`` — one-shot,
CLI exits after each step), :class:`InteractiveSession` keeps the CLI alive for
the whole step via ``ClaudeSDKClient``, and routes every tool call that reaches
"ask" through an injectable :class:`ToolApprovalHandler` (the ``can_use_tool``
callback).

Supported HITL scenarios (swap in the matching handler):
- Permission approval (:class:`TerminalApprovalHandler`): allow/deny each tool call
- Solution selection (:class:`TerminalChoiceHandler` + ``propose_options`` tool):
  user picks from candidates; the choice is written back via ``updated_input``
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from .handlers import ToolApprovalHandler, build_propose_hooks
from .tools import build_mcp_server


@dataclass
class InteractiveAgentSpec:
    """Declarative description of one interactive agent session."""

    name: str
    system_prompt: str
    model: str | None = None
    max_turns: int = 50
    mcp_servers: dict[str, Any] | None = None  # extra MCP servers (beyond propose_options)
    extra_options: dict[str, Any] = field(default_factory=dict)


class InteractiveSession:
    """A live ``ClaudeSDKClient`` session with ``can_use_tool`` routed to a handler.

    Usage::

        async with InteractiveSession(spec, handler) as s:
            final = await s.run("your prompt")

    The CLI stays alive from ``__aenter__`` to ``__aexit__``; any tool call the
    CLI would normally prompt for is routed to ``handler`` instead of the CLI's
    own permission prompt.

    Note: ``permission_mode`` defaults to ``"default"`` (set in ``__aenter__``).
    Do NOT override it to ``bypassPermissions`` — that auto-approves every tool
    call before the callback runs, so the handler would never fire.
    """

    def __init__(
        self,
        spec: InteractiveAgentSpec,
        handler: ToolApprovalHandler,
        *,
        include_propose_tool: bool = True,
    ) -> None:
        self.spec = spec
        self.handler = handler
        self.include_propose_tool = include_propose_tool
        self._client: ClaudeSDKClient | None = None

    async def __aenter__(self) -> "InteractiveSession":
        mcp_servers: dict[str, Any] = dict(self.spec.mcp_servers or {})
        if self.include_propose_tool:
            mcp_servers["interactive-tools"] = build_mcp_server()

        options = ClaudeAgentOptions(
            model=self.spec.model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            system_prompt=self.spec.system_prompt,
            permission_mode="default",  # NOT bypass — can_use_tool must trigger
            max_turns=self.spec.max_turns,
            can_use_tool=self.handler,  # the HITL hook
            **({"mcp_servers": mcp_servers} if mcp_servers else {}),
            # Force propose_options through can_use_tool — MCP tools are
            # auto-allowed otherwise and the choice handler would never fire.
            **({"hooks": build_propose_hooks()} if self.include_propose_tool else {}),
            **self.spec.extra_options,
        )
        client = ClaudeSDKClient(options=options)
        self._client = client
        await client.__aenter__()  # connect(None) — empty stream, CLI stays alive
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._client is not None:
            await self._client.__aexit__(exc_type, exc_val, exc_tb)
            self._client = None
        return False

    async def run(self, prompt: str) -> str:
        """Send a prompt and collect the final text (until ResultMessage)."""
        if self._client is None:
            raise RuntimeError("InteractiveSession not entered; use `async with`")
        await self._client.query(prompt)
        final = ""
        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        final = block.text  # keep latest assistant text
            elif isinstance(msg, ResultMessage):
                if msg.result:
                    final = msg.result  # authoritative final text
        return final
