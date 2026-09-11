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
from cua.domain.serde import dump_capability
from cua.recorder.compile import compile_capability
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
    vendor: Annotated[str, typer.Option(help="Vendor of the target product.")] = "acme-core",
    product: Annotated[str, typer.Option(help="Product name. Namespaces the capability id.")] = (
        "MemberDesk"
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

    result = compile_capability(
        run,
        vendor=vendor,
        product=product,
        entrypoint_url=target,
        transcript_ref=str(rig.run_dir / "trace.jsonl"),
    )

    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{result.capability.id}@{result.capability.version}.yaml"
    path.write_text(dump_capability(result.capability), encoding="utf-8")

    typer.secho("\nDiscovery succeeded.", fg=typer.colors.GREEN)
    typer.echo(f"  capability : {path}")
    typer.echo(f"  inputs     : {[spec.name for spec in result.capability.inputs]}")
    typer.echo(f"  outputs    : {[spec.name for spec in result.capability.outputs]}")

    for warning in result.warnings:
        typer.secho(f"  warning    : {warning}", fg=typer.colors.YELLOW)

    # The compiler declares no outcomes or recovery rules, because a successful run never saw a
    # failure path. Saying so here means the gap reaches whoever ran the command, not only whoever
    # opens the YAML.
    typer.secho(
        "\n  This is a DRAFT. It declares no business outcomes and no recovery rules -- a\n"
        "  successful run observes neither. Review the notes in the artifact before approving it\n"
        "  for unattended replay.",
        fg=typer.colors.YELLOW,
    )


@app.command()
def replay(
    artifact: Annotated[Path, typer.Argument(help="Path to a capability YAML.")],
    input: Annotated[  # noqa: A002 - reads naturally on the command line
        list[str] | None, typer.Option("--input", help="name=value, repeatable.")
    ] = None,
    base_url: Annotated[
        str | None, typer.Option(help="Bind {base_url} for this deployment.")
    ] = None,
    allow_irreversible: Annotated[
        bool, typer.Option(help="Caller opt-in for an irreversible capability.")
    ] = False,
    headless: Annotated[bool, typer.Option(help="Run the browser headless.")] = True,
) -> None:
    """Execute a saved capability deterministically. No model is involved."""
    from cua.domain.serde import load_capability
    from cua.domain.tenant_binding import TenantBinding
    from cua.replay.executor import ReplayExecutor
    from cua.runtime.capture import FailureCapture

    capability = load_capability(artifact.read_text(encoding="utf-8"))
    if base_url:
        capability = TenantBinding(
            capability_ref=capability.ref, tenant="cli", vars={"base_url": base_url}
        ).apply(capability)

    supplied: dict[str, str] = {}
    for pair in input or []:
        if "=" not in pair:
            typer.secho(f"--input expects name=value, got {pair!r}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        name, _, value = pair.partition("=")
        supplied[name] = value

    run_id = f"rep-{uuid.uuid4().hex[:10]}"
    rig = build_rig(run_id=run_id, kind="replay", headless=headless, allow_vision=False)
    try:
        executor = ReplayExecutor(
            dispatcher=rig.dispatcher,
            evidence=rig.evidence,
            capture=FailureCapture(driver=rig.driver, evidence=rig.evidence),
            allow_irreversible=allow_irreversible,
        )
        result = executor.run(capability, supplied)
    finally:
        rig.close()

    colour = {
        "success": typer.colors.GREEN,
        "business_outcome": typer.colors.CYAN,
        "needs_human": typer.colors.YELLOW,
        "failed": typer.colors.RED,
    }[result.status.value]
    typer.secho(f"\n{result.summary}", fg=colour)

    if result.outputs:
        typer.echo(f"  outputs  : {json.dumps(result.outputs, indent=2)}")
    if result.outcome:
        typer.echo(f"  data     : {json.dumps(result.outcome.data)}")
    if result.error and result.error.expected:
        typer.echo(f"  expected : {result.error.expected}")
        typer.echo(f"  observed : {result.error.observed}")
    typer.echo(f"  evidence : {rig.run_dir}")
    typer.echo(f"  drift    : {result.drift_score:.2f}   strategies: {result.strategy_mix}")

    # A business outcome exits 0: it is a successful execution that returned a negative answer.
    raise typer.Exit(code=result.exit_code)


@app.command()
def console(
    port: Annotated[int, typer.Option(help="Port for the operator console.")] = 8812,
    target: Annotated[str, typer.Option(help="URL to open in the supervised session.")] = (
        "http://localhost:8811/"
    ),
    headless: Annotated[bool, typer.Option(help="Run the supervised browser headless.")] = True,
) -> None:
    """Run the operator console over a supervised live session.

    Single operator, no authentication, local only -- a documented cut (REPORT.md §5). What is real:
    the operator drives the same Chromium session automation uses, their input travels the same
    policy chokepoint, and every action is recorded with actor=HUMAN.
    """
    import uvicorn

    from cua.hitl.console.server import ConsoleDeps, create_console
    from cua.runtime.wiring import build_supervised_session

    run_id = f"con-{uuid.uuid4().hex[:10]}"
    supervised = build_supervised_session(run_id=run_id, target=target, headless=headless)

    typer.secho(f"Operator console on http://127.0.0.1:{port}", fg=typer.colors.GREEN)
    typer.echo(f"  supervising : {target}")
    typer.echo(f"  evidence    : {supervised.run_dir}")
    try:
        uvicorn.run(
            create_console(
                ConsoleDeps(
                    broker=supervised.broker,
                    driver=supervised.driver,
                    session=supervised.session,
                )
            ),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    finally:
        supervised.close()


@app.command()
def version() -> None:
    from cua import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
