"""Example: a multi-agent evaluation flow driven by the runtime.

Flow::

    describe   (agent)  -> defect description
    judge      (agent)  -> LLM scores the description
    approve    (gate)   -> human approves / modifies the judgement   [HITL]
    human_fix  (human)  -> human supplies corrections               [HITL]
    report     (agent)  -> final summary

The first agent reads a seed task from a pre-filled ``__seed__`` output, which
shows how to inject external inputs without a dedicated step.

Run mock (no API cost — exercises flow + scheduler + checkpoint + resume)::

    python -m src.flow_engine.example_flow mock
    # ... hits the `approve` gate, checkpoints, prints a resume hint, exits.

    python -m src.flow_engine.example_flow mock --resume approve "APPROVED as-is"
    # ... hits `human_fix`, checkpoints, exits.

    python -m src.flow_engine.example_flow mock --resume human_fix "raise severity to high"
    # ... runs `report`, completes.

Run real (spends API budget; needs ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN)::

    python -m src.flow_engine.example_flow real
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from .agents import AgentSpec
from .runtime import InteractiveHitl, PauseResumeHitl, execute
from .types import Checkpoint, Flow, HumanSpec, PendingApproval, Step, StepOutput

CKPT_PATH = Path("results/flow_run.json")
SEED_ID = "__seed__"  # pseudo-output holding the initial task text


# --- Agent specs ---------------------------------------------------------

defect_agent = AgentSpec(
    name="defect-describer",
    system_prompt=(
        "You describe a software defect clearly: repro steps, expected vs "
        "actual, impact. Output a concise description only."
    ),
)
judge_agent = AgentSpec(
    name="llm-judge",
    system_prompt=(
        "You judge a defect description on clarity, completeness, and severity "
        "assessment. Reply with a one-line verdict plus a score 0-100."
    ),
)
reporter = AgentSpec(
    name="reporter",
    system_prompt=(
        "You write a short final report that combines the judgement and any "
        "human corrections."
    ),
)


# --- Flow ----------------------------------------------------------------

def build_flow() -> Flow:
    return Flow(
        steps=[
            Step(
                id="describe",
                kind="agent",
                agent=defect_agent,
                inputs={"task": f"{SEED_ID}.output"},
                prompt_template="Describe this defect:\n{task}",
            ),
            Step(
                id="judge",
                kind="agent",
                agent=judge_agent,
                inputs={"desc": "describe.output"},
                depends_on=["describe"],
                prompt_template="Judge this description:\n{desc}",
            ),
            Step(
                id="approve",
                kind="gate",
                inputs={"judge": "judge.output"},
                depends_on=["judge"],
                human=HumanSpec(
                    prompt="Approve the judgement above? Reply APPROVE / MODIFY / REJECT.",
                ),
            ),
            Step(
                id="human_fix",
                kind="human",
                inputs={"judge": "judge.output"},
                depends_on=["approve"],
                human=HumanSpec(
                    prompt="Type any corrections now (or 'none').",
                    output_hint="free-form corrections",
                ),
            ),
            Step(
                id="report",
                kind="agent",
                agent=reporter,
                inputs={"judge": "judge.output", "fix": "human_fix.output"},
                depends_on=["human_fix"],
                prompt_template=(
                    "Write the final report.\nJudgement:\n{judge}\n\nCorrections:\n{fix}"
                ),
            ),
        ]
    )


# --- Driver --------------------------------------------------------------

async def drive(
    mode: str,
    resume_step: str | None,
    resume_text: str | None,
    task: str,
    hitl_mode: str = "interactive",
) -> None:
    flow = build_flow()
    CKPT_PATH.parent.mkdir(parents=True, exist_ok=True)

    ckpt = (
        Checkpoint.load(str(CKPT_PATH))
        if CKPT_PATH.exists()
        else Checkpoint.new(run_id="demo", flow_id="eval-flow", path=str(CKPT_PATH))
    )
    if resume_step:
        ckpt.pending[resume_step] = resume_text or ""

    # Seed the initial task into a pseudo-output consumed by `describe`.
    if SEED_ID not in ckpt.completed:
        ckpt.completed[SEED_ID] = StepOutput(text=task)
        ckpt.save()

    if mode == "mock":
        from .agents import MockRunner

        runner = MockRunner(
            fn=lambda spec, p: f"[{spec.name}] mock output (prompt {len(p)} chars)"
        )
    else:
        from .agents import AgentRunner

        runner = AgentRunner()

    hitl = InteractiveHitl() if hitl_mode == "interactive" else PauseResumeHitl()

    try:
        results = await execute(flow, ckpt=ckpt, runner=runner, hitl=hitl)
    except PendingApproval as pa:
        ckpt.save()
        preview = str(pa.context)[:160]
        print(f"\n[HITL] paused at step {pa.step_id!r}: {pa.prompt}")
        print(f"        context preview: {preview}…")
        print(f"        resume with: --resume {pa.step_id} '<your decision>'")
        return

    print("\n=== FLOW COMPLETE ===")
    for sid, out in results.items():
        if sid.startswith("__"):
            continue  # skip pseudo outputs
        print(f"\n--- {sid} ---\n{out.text}")
    print(f"\n(checkpoint at {CKPT_PATH})")


def main() -> None:
    p = argparse.ArgumentParser(description="Run the example flow.")
    p.add_argument("mode", choices=["mock", "real"])
    p.add_argument(
        "--hitl",
        choices=["interactive", "pause"],
        default="interactive",
        help="how HITL steps collect a decision (default: interactive terminal input)",
    )
    p.add_argument(
        "--resume",
        nargs="+",
        metavar=("STEP", "DECISION"),
        help="step id to resume, optionally followed by the decision text",
    )
    p.add_argument(
        "--task",
        default="Login button on /checkout throws 500 when the cart has >50 items.",
        help="seed task for the first agent step",
    )
    args = p.parse_args()
    resume = args.resume
    resume_step = resume[0] if resume else None
    resume_text = " ".join(resume[1:]) if resume and len(resume) > 1 else None
    asyncio.run(drive(args.mode, resume_step, resume_text, args.task, args.hitl))


if __name__ == "__main__":
    main()
