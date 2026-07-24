"""Interactive runtime — tool-level HITL on top of ClaudeSDKClient.

Unlike ``flow_engine`` (inter-step HITL; the CLI exits between steps and the
runtime asks the human on its own), this runtime keeps the CLI alive for a
whole agent session and routes every tool call that reaches "ask" through an
injectable :class:`ToolApprovalHandler` via the SDK's ``can_use_tool`` callback.

Two built-in scenarios:
- Permission approval: :class:`TerminalApprovalHandler` (allow/deny each tool call)
- Solution selection:   :class:`TerminalChoiceHandler` + ``propose_options`` tool
  (user picks from candidates; the choice is written back via ``updated_input``)

Pipeline::

    InteractiveSession(__aenter__)
      -> ClaudeSDKClient(options, can_use_tool=handler)   # connect(None), CLI alive
      -> client.query(prompt)                              # send the task
      -> client.receive_response()                         # stream messages
           tool call reaches "ask"
             -> CLI sends control_request (can_use_tool) over stdio
             -> SDK awaits handler(tool_name, input, ctx) # HITL on the app layer
             -> handler returns Allow / Deny / Allow(updated_input=...)
             -> SDK writes control_response back to CLI
             -> CLI proceeds (executes the tool with updated_input, or skips)
      -> ResultMessage                                     # step done
    InteractiveSession(__aexit__)                          # disconnect, CLI exits
"""
from .handlers import (
    TerminalApprovalHandler,
    TerminalChoiceHandler,
    ToolApprovalHandler,
)
from .session import (
    InteractiveAgentSpec,
    InteractiveSession,
    SkillsAgentSpec,
    SkillsSession,
)
from .tools import build_mcp_server, propose_options

__all__ = [
    "InteractiveAgentSpec",
    "InteractiveSession",
    "SkillsAgentSpec",
    "SkillsSession",
    "TerminalApprovalHandler",
    "TerminalChoiceHandler",
    "ToolApprovalHandler",
    "build_mcp_server",
    "propose_options",
]
