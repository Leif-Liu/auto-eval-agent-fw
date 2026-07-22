"""Tool-level HITL handlers for the interactive runtime.

A :class:`ToolApprovalHandler` is invoked by ``ClaudeSDKClient``'s
``can_use_tool`` callback each time the CLI's permission rules evaluate to
"ask" for a tool call. The handler decides allow / deny / rewrite-input and
returns a ``PermissionResult``.

This is the *intra-step* HITL layer — the CLI stays alive for the whole
agent step, and the decision point is a tool call *inside* the step. Contrast
with ``flow_engine``'s *inter-step* ``HitlHandler``, where the CLI has already
exited by the time the human is asked.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol, runtime_checkable

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)


@runtime_checkable
class ToolApprovalHandler(Protocol):
    """Resolve a tool call into a permission decision (allow / deny / rewrite).

    Invoked by ``ClaudeSDKClient``'s ``can_use_tool`` for tool calls that reach
    the "ask" state. Returns ``PermissionResultAllow`` (optionally with
    ``updated_input`` to rewrite the tool's input) or ``PermissionResultDeny``.
    """

    async def __call__(
        self, tool_name: str, tool_input: dict[str, Any], ctx: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny: ...


def _render_request(
    tool_name: str, tool_input: dict[str, Any], ctx: ToolPermissionContext
) -> str:
    """Build the human-readable prompt describing the pending tool call."""
    title = ctx.title or f"Claude wants to run {tool_name}"
    lines = [f"[approve] {title}"]
    if ctx.display_name or ctx.description:
        sub = " — ".join(p for p in (ctx.display_name, ctx.description) if p)
        lines.append(f"        {sub}")
    if tool_input:
        preview = json.dumps(tool_input, ensure_ascii=False)
        if len(preview) > 300:
            preview = preview[:300] + " …"
        lines.append(f"        input: {preview}")
    if ctx.blocked_path:
        lines.append(f"        blocked_path: {ctx.blocked_path}")
    if ctx.decision_reason:
        lines.append(f"        reason: {ctx.decision_reason}")
    return "\n".join(lines)


async def _read_line(prompt: str) -> str | None:
    """Read a line from stdin off the event loop; None on EOF (non-TTY).

    ``input()`` blocks the OS thread, so it runs in a thread worker via
    ``asyncio.to_thread`` to keep the event loop responsive. A closed stdin
    (CI / background run) raises ``EOFError`` — we return ``None`` so callers
    can degrade to a safe default instead of crashing.
    """
    try:
        return await asyncio.to_thread(input, prompt)
    except EOFError:
        return None


class TerminalApprovalHandler:
    """Permission-approval handler: ask allow/deny in the terminal.

    For the "权限批准" scenario — the agent wants to call a tool (bash/write/…)
    and the handler prints the request and reads an allow/deny decision from
    stdin. EOF / empty / non-"y" input is treated as **deny** (conservative).
    """

    PROMPT = "  allow? (y/N) > "

    async def __call__(
        self, tool_name: str, tool_input: dict[str, Any], ctx: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        print(_render_request(tool_name, tool_input, ctx))
        raw = await _read_line(self.PROMPT)
        choice = (raw or "").strip().lower()
        if choice in ("y", "yes", "allow"):
            return PermissionResultAllow()
        return PermissionResultDeny(message=f"user denied {tool_name}")


class TerminalChoiceHandler:
    """Solution-selection handler: let the user pick from proposed options.

    For the "方案选择" scenario — the agent calls the ``propose_options`` tool
    with a list of candidate solutions; the handler displays them, the user
    picks one, and the chosen option is written back via ``updated_input`` so
    the tool returns only the selection to the agent.

    Non-``propose_options`` tool calls are auto-allowed (the propose flow is
    the only HITL point in this handler); swap in a stricter handler if you
    want every tool gated.
    """

    PROPOSE_TOOL = "propose_options"

    async def __call__(
        self, tool_name: str, tool_input: dict[str, Any], ctx: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        _ = ctx  # ctx unused here; kept for ToolApprovalHandler protocol parity
        if self.PROPOSE_TOOL not in tool_name:  # covers mcp__<server>__propose_options
            return PermissionResultAllow()

        options = tool_input.get("options") or []
        if not options:
            return PermissionResultDeny(message="propose_options received no options")

        print("\n[choose] Claude proposed the following solutions:")
        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt}")

        n = len(options)
        raw = await _read_line(f"  pick 1-{n} > ")
        if raw is None:
            return PermissionResultDeny(message="no choice (stdin EOF)")
        try:
            idx = int(raw.strip())
            if not 1 <= idx <= n:
                raise ValueError
        except ValueError:
            return PermissionResultDeny(
                message=f"invalid choice {raw!r}; must be 1-{n}"
            )

        chosen = options[idx - 1]
        # Rewrite the tool input so the tool returns only the chosen option.
        return PermissionResultAllow(
            updated_input={"options": [chosen], "selected": chosen}
        )


# --- PreToolUse hook: force propose_options into the can_use_tool callback ---
#
# Custom MCP tools are auto-allowed by the CLI and skip the can_use_tool
# callback (SDK types.py:1662-1673), so TerminalChoiceHandler never fires for
# propose_options. This PreToolUse hook returns permissionDecision="ask" for
# propose_options — which routes the call back through can_use_tool
# (SDK types.py:220 confirms a PreToolUse "ask" triggers the callback). For
# any other tool the hook is a no-op, so the approval scenario (bash/read,
# already "ask" by default) is unaffected.

async def force_ask_propose_options(
    hook_input: Any, tool_use_id: str | None, context: Any
) -> dict[str, Any]:
    """PreToolUse hook routing ``propose_options`` back to ``can_use_tool``."""
    _ = tool_use_id, context  # HookCallback signature parity; unused here
    tool_name = (hook_input.get("tool_name") if isinstance(hook_input, dict) else "") or ""
    # MCP tools arrive as "mcp__<server>__propose_options" — match by suffix.
    if "propose_options" in tool_name:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": "User must select a solution.",
            }
        }
    return {}  # no opinion -> default behavior


def build_propose_hooks() -> dict[str, list]:
    """Build the hooks dict forcing propose_options through can_use_tool.

    ``matcher=None`` matches every tool; the hook filters on
    ``tool_name == "propose_options"`` internally and is a no-op otherwise.
    """
    from claude_agent_sdk import HookMatcher  # lazy: keep mock-mode import-free
    return {"PreToolUse": [HookMatcher(matcher=None, hooks=[force_ask_propose_options])]}
