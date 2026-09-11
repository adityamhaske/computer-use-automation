"""Command line entry points."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Annotated

import typer

from cua.agent.llm import LlmError, OpenRouterLlm
from cua.agent.loop import DiscoveryAgent
from cua.agent.stop import Budget
from cua.runtime.wiring import build_rig

app = typer.Typer(
    add_completion=False,
    help="Computer-use automation: discover once with an LLM, replay deterministically.",
)


@app.command()
def discover(
    goal: Annotated[str, typer.Option(help="What to accomplish, in plain language.")],
    target: Annotated[str, typer.Option(help="Entry-point URL of the application.")],
    out: Annotated[Path, typer.Option(help="Where to write the capability.")] = Path(
        "evidence/capabilities"
    ),
    max_steps: Annotated[int, typer.Option(help="Hard cap on model turns.")] = 40,
    headless: Annotated[bool, typer.Option(help="Run the browser headless.")] = True,
) -> None:
    """Drive a live application with a model until the goal is met, and record what worked."""
    run_id = f"disc-{uuid.uuid4().hex[:10]}"

    try:
        llm = OpenRouterLlm()
    except LlmError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    rig = build_rig(run_id=run_id, kind="discovery", headless=headless, allow_vision=True)
    try:
        rig.driver.page.goto(target, wait_until="load")
        agent = DiscoveryAgent(
            llm=llm,
            dispatcher=rig.dispatcher,
            evidence=rig.evidence,
            redactor=rig.redactor,
            budget=Budget(max_steps=max_steps),
        )
        run = agent.run(goal=goal, target_url=target)
    finally:
        rig.close()

    typer.echo(f"\n{run.stop_reason.value.upper()} after {len(run.steps)} step(s)")
    typer.echo(f"  evidence : {rig.run_dir}")
    if run.outputs:
        typer.echo(f"  outputs  : {json.dumps(run.outputs, indent=2)}")
    if run.checkpoint_hint:
        typer.echo(f"  checkpoint hint: {run.checkpoint_hint}")

    if not run.succeeded:
        typer.secho(f"  {run.summary}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    # Compiling the trace into a capability arrives in Phase 07; the trace it needs is already
    # recorded, so the discovery half stands on its own.
    typer.secho("\nDiscovery succeeded.", fg=typer.colors.GREEN)
    typer.echo(f"  artifact compilation lands in Phase 07; trace is in {rig.run_dir}/trace.jsonl")
    _ = out


@app.command()
def version() -> None:
    from cua import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
