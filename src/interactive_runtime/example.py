"""Example: tool-level HITL with the interactive runtime.

Two scenarios, each keeps the CLI alive for the whole step and routes tool
calls through a ``can_use_tool`` handler:

Scenario 1 — permission approval (``approve``):
    The agent inspects files via bash/read; :class:`TerminalApprovalHandler`
    asks allow/deny for each tool call that reaches "ask".

Scenario 2 — solution selection (``choose``):
    The agent calls the ``propose_options`` tool with candidate solutions;
    :class:`TerminalChoiceHandler` displays them, the user picks one, and the
    choice is written back via ``updated_input`` so the tool returns only the
    selection.

Run (needs ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_MODEL``
in ``.env`` or the environment; the handler must trigger, so permission_mode is
``default`` — never ``bypassPermissions``)::

    python -m src.interactive_runtime.example approve
    python -m src.interactive_runtime.example choose
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

from .handlers import TerminalApprovalHandler, TerminalChoiceHandler
from .session import InteractiveAgentSpec, InteractiveSession

load_dotenv()  # so .env reaches os.environ for the spawned CLI subprocess


APPROVE_SPEC = InteractiveAgentSpec(
    name="approve-demo",
    system_prompt=(
        "You help the user inspect a project. Use bash to list files and read "
        "to inspect them when useful. Keep responses short."
    ),
)

CHOOSE_SPEC = InteractiveAgentSpec(
    name="choose-demo",
    system_prompt=(
        "You are deciding how to fix a flaky test. Use the propose_options tool "
        "to submit 3 candidate solutions (short one-liners each), then act on "
        "the one the user selected and summarize it in one sentence."
    ),
)


async def run_approve() -> None:
    print("=== Scenario 1: permission approval ===")
    async with InteractiveSession(
        APPROVE_SPEC, TerminalApprovalHandler(),
        include_propose_tool=False, force_ask_all=True,
    ) as s:
        final = await s.run(
            "Use the bash tool to run `find . -name '*.py' | wc -l` and "
            "report the count of Python files."
        )
    print("\n--- final ---")
    print(final)


async def run_choose() -> None:
    print("=== Scenario 2: solution selection ===")
    async with InteractiveSession(CHOOSE_SPEC, TerminalChoiceHandler()) as s:
        final = await s.run(
            "A CI test 'test_retry' flakes ~10% of the time. Propose 3 "
            "candidate fixes via propose_options, then summarize the one I picked."
        )
    print("\n--- final ---")
    print(final)


def main() -> None:
    p = argparse.ArgumentParser(description="Interactive runtime demo.")
    p.add_argument("scenario", choices=["approve", "choose"])
    args = p.parse_args()
    if not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        print(
            "ANTHROPIC_AUTH_TOKEN not set; configure .env or export it.",
            file=sys.stderr,
        )
        sys.exit(1)
    if args.scenario == "approve":
        asyncio.run(run_approve())
    else:
        asyncio.run(run_choose())


if __name__ == "__main__":
    main()
