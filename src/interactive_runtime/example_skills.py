"""Example: skills-driven autonomous agent (ClaudeSDKClient + project skill).

Demonstrates loading a project skill (``feature-dev-eval-agent-fw``) and
running an agent autonomously (no HITL) to drive the feature-dev workflow for
a feature described on the CLI. The CLI stays alive via ClaudeSDKClient; the
agent uses the skill to run the seven-phase workflow.

Run (needs ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN`` /
``ANTHROPIC_MODEL`` in ``.env`` or the environment)::

    python -m src.interactive_runtime.example_skills "新增一个评估维度：代码复杂度"
    python -m src.interactive_runtime.example_skills --skill feature-dev-eval-agent-fw "你的功能描述"
    python -m src.interactive_runtime.example_skills --skill all "你的功能描述"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

from .session import SkillsAgentSpec, SkillsSession

load_dotenv()  # so .env reaches os.environ for the spawned CLI subprocess

DEFAULT_SKILL = "feature-dev-eval-agent-fw"  # project skill in .claude/skills/


def _build_spec(skill: str) -> SkillsAgentSpec:
    """Build a spec whose system_prompt matches the enabled skill.

    System prompt references the actual skill (no hardcoded name), so
    ``--skill <other>`` doesn't tell the agent to use a skill the SDK has
    filtered out (skills is a context filter, not a sandbox).
    """
    skill_label = "all available skills" if skill == "all" else f"the {skill} skill"
    return SkillsAgentSpec(
        name="skills-demo",
        system_prompt=(
            "You are a senior developer on the auto-eval-agent-fw project. Use "
            f"{skill_label} to drive the feature-dev workflow for the requested "
            "feature. Follow the skill's seven phases. Work autonomously — the "
            "user already gave the feature description."
        ),
        skills="all" if skill == "all" else [skill],
    )


async def run_skills(task: str, skill: str) -> None:
    print(f"=== skills-driven demo (skill={skill!r}) ===")
    print(f"task: {task}\n")
    spec = _build_spec(skill)
    final = ""
    async with SkillsSession(spec, trace=True) as s:  # trace: tool_use → stderr
        final = await s.run(task)
    print("\n--- final ---")
    print(final)


def main() -> None:
    p = argparse.ArgumentParser(description="Skills-driven autonomous agent demo.")
    p.add_argument("task", help="feature description for the agent to develop")
    p.add_argument(
        "--skill",
        default=DEFAULT_SKILL,
        help=f"skill name (default: {DEFAULT_SKILL}); or 'all' for every skill",
    )
    args = p.parse_args()
    if not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        print(
            "ANTHROPIC_AUTH_TOKEN not set; configure .env or export it.",
            file=sys.stderr,
        )
        sys.exit(1)
    asyncio.run(run_skills(args.task, args.skill))


if __name__ == "__main__":
    main()
