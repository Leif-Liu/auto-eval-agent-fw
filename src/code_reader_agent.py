"""Code-reader agent (Claude Agent SDK edition).

Uses `claude_agent_sdk.query` with an in-process SDK MCP server that exposes
four file-system tools (list_files / read_file / search_code /
get_project_architecture) scoped to the project root. The underlying Claude
Code CLI is driven by the SDK and authenticates via ANTHROPIC_BASE_URL /
ANTHROPIC_AUTH_TOKEN from the environment.

Run:
    python -m src.code_reader_agent
    # or
    python src/code_reader_agent.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    query,
    tool,
)
from dotenv import load_dotenv

# Load variables from .env before anything else so the Claude Code CLI (spawned
# by the SDK) inherits ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN.
load_dotenv()

# Project root = parent of this file's parent (src/ -> repo root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Model ID: try env var first, fall back to a sane default.
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

SYSTEM_PROMPT = f"""\
You are a senior code-architect reviewer exploring the project at:
  {PROJECT_ROOT}

This project is the "Defect Description Agent Evaluation Framework". Use the
provided tools to investigate the codebase, then give a concise architecture
summary covering:
  1. Module layout and responsibilities
  2. Data flow through the evaluation pipeline
  3. Key abstractions and design decisions
  4. Anything that looks risky, incomplete, or worth improving

Be concrete: cite file paths and line numbers you actually read. Do not guess
about files you have not opened. Keep the final summary under ~400 words."""


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def _safe_path(path: str) -> Path:
    """Resolve `path` against PROJECT_ROOT and reject escapes."""
    resolved = (PROJECT_ROOT / path).resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise PermissionError(f"path escapes project root: {path}") from exc
    return resolved


# ---------------------------------------------------------------------------
# Tools (registered as an in-process SDK MCP server)
# ---------------------------------------------------------------------------

_LIST_FILES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "directory": {
            "type": "string",
            "default": "src",
            "description": "Relative path from project root (e.g. src, config, test_data).",
        }
    },
    "required": [],
}

_READ_FILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Relative path from project root (e.g. src/orchestrator.py, arch.md).",
        }
    },
    "required": ["path"],
}

_SEARCH_CODE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Regular expression to search for.",
        },
        "path": {
            "type": "string",
            "default": "src",
            "description": "Directory (relative to project root) to search in.",
        },
    },
    "required": ["pattern"],
}

_GET_ARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}


def _text(out: str, *, is_error: bool = False) -> dict[str, Any]:
    """Build an MCP tool result payload with a single text block."""
    payload: dict[str, Any] = {"content": [{"type": "text", "text": out}]}
    if is_error:
        payload["is_error"] = True
    return payload


@tool("list_files", "Recursively list files under a directory in the project.", _LIST_FILES_SCHEMA)
async def list_files(args: dict[str, Any]) -> dict[str, Any]:
    directory = args.get("directory") or "src"
    root = _safe_path(directory)
    if not root.exists():
        return _text(f"ERROR: not found: {directory}")
    if not root.is_dir():
        return _text(f"ERROR: not a directory: {directory}")

    lines: list[str] = []
    skip_parts = {"__pycache__", ".git", "results"}
    for p in sorted(root.rglob("*")):
        if any(part in skip_parts or part.endswith(".egg-info") for part in p.parts):
            continue
        rel = p.relative_to(PROJECT_ROOT)
        lines.append(f"{rel}/" if p.is_dir() else str(rel))
    return _text("\n".join(lines) or f"(empty: {directory})")


@tool("read_file", "Read the full contents of a file in the project.", _READ_FILE_SCHEMA)
async def read_file(args: dict[str, Any]) -> dict[str, Any]:
    path = args["path"]
    file_path = _safe_path(path)
    if not file_path.exists():
        return _text(f"ERROR: not found: {path}")
    if not file_path.is_file():
        return _text(f"ERROR: not a file: {path}")
    try:
        return _text(file_path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return _text(f"ERROR: {exc}")


@tool("search_code", "Grep for a regex pattern in files under a directory.", _SEARCH_CODE_SCHEMA)
async def search_code(args: dict[str, Any]) -> dict[str, Any]:
    import re

    pattern = args["pattern"]
    path = args.get("path") or "src"
    root = _safe_path(path)
    if not root.exists() or not root.is_dir():
        return _text(f"ERROR: not a directory: {path}")

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return _text(f"ERROR: bad regex: {exc}")

    hits: list[str] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix not in {".py", ".md", ".toml", ".json", ".yaml", ".yml", ".txt"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                rel = p.relative_to(PROJECT_ROOT)
                hits.append(f"{rel}:{i}: {line.strip()}")
        if len(hits) > 200:
            hits.append("... (truncated at 200 matches)")
            break
    return _text("\n".join(hits) or "(no matches)")


@tool(
    "get_project_architecture",
    "Return the project's arch.md (architecture overview) verbatim.",
    _GET_ARCH_SCHEMA,
)
async def get_project_architecture(args: dict[str, Any]) -> dict[str, Any]:
    arch = PROJECT_ROOT / "arch.md"
    if not arch.exists():
        return _text("ERROR: arch.md not found at project root")
    return _text(arch.read_text(encoding="utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _build_options(
    *,
    extra_agents: dict[str, AgentDefinition] | None = None,
) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions.

    Pass ``extra_agents`` to register programmatic (runtime) subagents
    invokable via the Agent tool alongside any disk-based agents in
    ``.claude/agents/`` (which are auto-loaded because setting_sources
    defaults to None = all sources). When non-empty, the ``Agent`` tool
    is added to allowed_tools so the model can dispatch subagents.
    Default behavior is unchanged when extra_agents is None.
    """
    server = create_sdk_mcp_server(
        name="code-reader",
        version="1.0.0",
        tools=[list_files, read_file, search_code, get_project_architecture],
    )
    needs_agent_tool = bool(extra_agents)
    return ClaudeAgentOptions(
        model=DEFAULT_MODEL,
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"code-reader": server},
        # Custom in-process tools still go through the permission flow; bypass
        # so the agent can run non-interactively.
        permission_mode="bypassPermissions",
        cwd=str(PROJECT_ROOT),
        max_turns=50,
        # "Agent" is the tool name for dispatching subagents. Only added when
        # extra_agents is set so default runs stay tool-set-minimal.
        **({"allowed_tools": ["Agent"]} if needs_agent_tool else {}),
        **({"agents": extra_agents} if extra_agents is not None else {}),
    )


def _default_prompt() -> str:
    return (
        "Read this project's architecture. Start by calling "
        "get_project_architecture, then list_files on src/ and config/, "
        "and read the key modules (orchestrator, agent_client, llm_judge, "
        "the evaluation/ modules, reporting/, and cli). Finish with the "
        "summary described in the system prompt."
    )


def _preview(text: str, width: int = 200) -> str:
    """One-line preview of a potentially long string."""
    single = " ".join(text.split())
    return single if len(single) <= width else single[:width] + " …"


async def run_agent(
    user_prompt: str | None = None,
    *,
    verbose: bool = False,
    extra_agents: dict[str, AgentDefinition] | None = None,
) -> str:
    """Run the code-reader agent loop and return the final text.

    When `verbose` is True, print a trace of every message yielded by
    `query` — AssistantMessage text/tool_use blocks, UserMessage
    tool_result blocks, SystemMessage subtypes, and the terminal
    ResultMessage — so the full agent loop (tool call → tool result →
    next assistant turn) is visible.

    When `extra_agents` is provided, those programmatic subagents are
    registered alongside disk-based agents in `.claude/agents/` (auto-
    loaded via the default setting_sources). The caller's prompt can
    then name any of them for the model to dispatch via the Agent tool.
    """
    options = _build_options(extra_agents=extra_agents)
    prompt = user_prompt or _default_prompt()

    final_text = ""
    turn = 0
    async for message in query(prompt=prompt, options=options):
        kind = type(message).__name__
        turn += 1

        if verbose:
            print(f"[turn {turn:02d}] >>> {kind}")

        if isinstance(message, AssistantMessage):
            if verbose and message.model:
                print(f"          model={message.model} stop_reason={message.stop_reason}")
            for block in message.content:
                if isinstance(block, TextBlock):
                    if verbose:
                        print(f"          · text: {_preview(block.text)}")
                    final_text = block.text  # keep the latest text block
                elif isinstance(block, ToolUseBlock):
                    if verbose:
                        raw = block.input
                        input_preview = _preview(str(raw)) if raw else "(empty)"
                        print(f"          · tool_use: {block.name} id={block.id} input={input_preview}")
                elif isinstance(block, ThinkingBlock):
                    if verbose:
                        print(f"          · thinking: {_preview(block.thinking)}")
                elif verbose:
                    print(f"          · {type(block).__name__}: (unhandled block)")
        elif isinstance(message, UserMessage):
            if verbose:
                content = getattr(message, "content", "")
                if isinstance(content, str):
                    print(f"          · user text: {_preview(content)}")
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, ToolResultBlock):
                            raw = block.content
                            if isinstance(raw, list):
                                text = " ".join(
                                    getattr(b, "text", str(b)) for b in raw
                                )
                            else:
                                text = str(raw) if raw else "(empty)"
                            print(
                                f"          · tool_result: id={block.tool_use_id}"
                                f" is_error={block.is_error} → {_preview(text)}"
                            )
                        elif isinstance(block, TextBlock):
                            print(f"          · text: {_preview(block.text)}")
                        else:
                            print(f"          · {type(block).__name__}: (non-tool block)")
        elif isinstance(message, SystemMessage):
            if verbose:
                print(f"          · subtype={message.subtype} data={_preview(str(message.data))}")
        elif isinstance(message, ResultMessage):
            if message.result:
                final_text = message.result
            if verbose:
                print(
                    f"          · subtype={message.subtype} num_turns={message.num_turns}"
                    f" duration_ms={message.duration_ms} is_error={message.is_error}"
                    f" stop_reason={message.stop_reason}"
                )
                if message.result:
                    print(f"          · result: {_preview(message.result)}")
        else:
            # StreamEvent / RateLimitInfo / others: not surfaced
            if verbose:
                print(f"          · (ignored)")
    return final_text


def _mixed_test_prompt() -> str:
    """Prompt for the mixed runtime/disk subagent dispatch test."""
    return (
        "Dispatch BOTH subagents: (1) code-reviewer to review src/cli.py "
        "for 2 findings, (2) architecture-flow-analyzer to list its 5 "
        "methodology phases. Then combine outputs in 3 sentences."
    )


def _runtime_reviewer() -> AgentDefinition:
    """Runtime (programmatic) code-reviewer subagent for the mix test."""
    return AgentDefinition(
        description=(
            "Expert code reviewer. Use for code quality findings with "
            "file:line citations."
        ),
        prompt="Review code briefly. Cite file:line. Max 3 findings.",
        tools=["Read", "Glob", "Grep"],
        model="inherit",
    )


def main() -> None:
    # mode: "default" (no args) or "mix" (mixed runtime/disk subagent test).
    mode = sys.argv[1] if len(sys.argv) > 1 else "default"
    print(f"[code-reader-agent] model={DEFAULT_MODEL}")
    print(f"[code-reader-agent] base_url={os.environ.get('ANTHROPIC_BASE_URL')}")
    print(f"[code-reader-agent] mode={mode}")
    print("-" * 60)
    if mode == "mix":
        result = asyncio.run(
            run_agent(
                user_prompt=_mixed_test_prompt(),
                verbose=True,
                extra_agents={"code-reviewer": _runtime_reviewer()},
            )
        )
    elif mode == "default":
        result = asyncio.run(run_agent(verbose=True))
    else:
        print(f"unknown mode: {mode!r} (expected 'default' or 'mix')")
        return
    print("-" * 60)
    print(result)


if __name__ == "__main__":
    main()
