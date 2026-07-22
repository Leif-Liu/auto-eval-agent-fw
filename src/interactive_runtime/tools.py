"""In-process SDK MCP tools for the interactive runtime.

``propose_options`` lets the agent submit candidate solutions; the actual
selection happens in :class:`TerminalChoiceHandler` (the ``can_use_tool``
callback rewrites the tool input via ``updated_input``), and the tool itself
just echoes back what it received so the agent sees the chosen option.
"""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

_PROPOSE_OPTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "options": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Candidate solutions to present to the user for selection.",
        }
    },
    "required": ["options"],
}


@tool(
    "propose_options",
    "Propose candidate solutions for the user to pick one. Call this when you "
    "have multiple viable approaches and the user should decide which to take.",
    _PROPOSE_OPTIONS_SCHEMA,
)
async def propose_options(args: dict[str, Any]) -> dict[str, Any]:
    """Echo the (possibly rewritten) options back to the agent.

    When used with :class:`TerminalChoiceHandler`, ``args`` arrives already
    rewritten to contain only the chosen option (plus a ``selected`` field).
    We surface that back as plain text so the agent knows the user's pick.
    """
    selected = args.get("selected")
    options = args.get("options") or []
    if selected is not None:
        text = f"User selected: {selected}"
    else:
        text = f"Options presented (no selection recorded): {options}"
    return {"content": [{"type": "text", "text": text}]}


def build_mcp_server():
    """Build the in-process MCP server exposing the propose_options tool."""
    return create_sdk_mcp_server(
        name="interactive-tools",
        version="1.0.0",
        tools=[propose_options],
    )
