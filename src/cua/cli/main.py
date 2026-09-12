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
from cua.domain.capability import Capability
from cua.domain.result import RunResult
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
    result, run_dir = _replay_once(
        capability=capability,
        inputs=supplied,
        headless=headless,
        run_id=run_id,
        allow_irreversible=allow_irreversible,
    )

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
    typer.echo(f"  evidence : {run_dir}")
    typer.echo(f"  drift    : {result.drift_score:.2f}   strategies: {result.strategy_mix}")

    # A business outcome exits 0: it is a successful execution that returned a negative answer.
    raise typer.Exit(code=result.exit_code)


def _replay_once(
    *,
    capability: Capability,
    inputs: dict[str, str],
    run_id: str,
    headless: bool = True,
    base_url: str | None = None,
    allow_irreversible: bool = False,
) -> tuple[RunResult, Path]:
    """Execute one capability and return its result and evidence directory.

    Shared by `cua replay` and the calling-agent demo so the two cannot drift apart. A demo that
    took a different path to the executor would be demonstrating something other than what the CLI
    does, which is the failure mode of most "example" code.
    """
    from cua.domain.tenant_binding import TenantBinding
    from cua.replay.executor import ReplayExecutor
    from cua.runtime.capture import FailureCapture

    if base_url:
        capability = TenantBinding(
            capability_ref=capability.ref, tenant="cli", vars={"base_url": base_url}
        ).apply(capability)

    rig = build_rig(run_id=run_id, kind="replay", headless=headless, allow_vision=False)
    try:
        result = ReplayExecutor(
            dispatcher=rig.dispatcher,
            evidence=rig.evidence,
            capture=FailureCapture(driver=rig.driver, evidence=rig.evidence),
            allow_irreversible=allow_irreversible,
        ).run(capability, inputs)
    finally:
        rig.close()
    return result, rig.run_dir


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
def demo(
    headless: Annotated[bool, typer.Option(help="Run the browser headless.")] = True,
) -> None:
    """Run the whole story end to end and leave the evidence behind.

    Discovery, artifact compilation, deterministic replay, a business outcome, an injected fault
    recovered, a fail-closed escalation, a human takeover on the same live session, and a
    re-anchored resume. Works without an API key -- discovery falls back to a recorded transcript
    and says so.
    """
    from cua.cli.demo import run_demo

    raise typer.Exit(code=run_demo(headless=headless))


@app.command()
def eval(  # noqa: A001 -- the command is `cua eval`; shadowing the builtin is local to this module
    suite: Annotated[
        str, typer.Option(help="Suite to run: replay_stability, cross_tenant, or all.")
    ] = "all",
    repeats: Annotated[int, typer.Option(help="How many times to replay each case.")] = 5,
    headless: Annotated[bool, typer.Option(help="Run the browser headless.")] = True,
) -> None:
    """Measure what the write-up claims, and write the reports into evidence/evals/.

    The headline is the wrong-action rate, and its target is zero. A system that refuses is
    acceptable -- it escalates with full context. A system that clicks the wrong row in a core
    banking screen is an incident, so refusal rate and wrong-action rate are reported separately and
    never traded off against each other.
    """
    from cua.evals import report
    from cua.evals.suites import SUITES

    names = sorted(SUITES) if suite == "all" else [suite]
    unknown = [name for name in names if name not in SUITES]
    if unknown:
        typer.secho(
            f"unknown suite(s): {', '.join(unknown)}; expected {', '.join(sorted(SUITES))}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    sound = True
    for name in names:
        typer.secho(f"\n{name}", bold=True)
        result = SUITES[name](repeats=repeats, headless=headless)
        evaluation = report.evaluate(result)
        written = report.write(result)
        unauthorized = sum(len(r.unauthorized_dispatches) for r in result.records)

        ok = (
            evaluation.wrong_actions == 0
            and unauthorized == 0
            and evaluation.determinism_holds is not False
        )
        sound = sound and ok
        typer.secho(
            f"  wrong actions: {evaluation.wrong_actions}   "
            f"unauthorized: {unauthorized}   "
            f"determinism: {evaluation.determinism_holds}   "
            f"runs: {evaluation.runs}",
            fg=typer.colors.GREEN if ok else typer.colors.RED,
        )
        typer.echo(f"  strategies: {evaluation.strategy_mix}")
        typer.echo(f"  report: {written['markdown']}")

    typer.echo()
    raise typer.Exit(code=0 if sound else 1)


catalog_app = typer.Typer(help="The capabilities an agent can call, and their typed signatures.")
app.add_typer(catalog_app, name="catalog")


@catalog_app.command("list")
def catalog_list() -> None:
    """What this deployment can do, as a calling agent would see it."""
    from cua.catalog.store import CapabilityStore

    entries = CapabilityStore().list()
    if not entries:
        typer.secho(
            "the catalog is empty -- run `make demo` or `cua discover` to produce a capability",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=0)

    for path, why in CapabilityStore().unreadable:
        typer.secho(f"  ! {path.name} could not be read: {why}", fg=typer.colors.RED)

    for entry in entries:
        typer.secho(f"\n{entry.ref}  [{entry.state}]", bold=True)
        typer.echo(f"  {entry.capability.title}")
        typer.echo(f"  {entry.signature}")
        if entry.capability.outcomes:
            codes = ", ".join(o.code for o in entry.capability.outcomes)
            typer.echo(f"  outcomes: {codes}")
    typer.echo()


@catalog_app.command("show")
def catalog_show(
    ref: Annotated[str, typer.Argument(help="Capability `id` or `id@version`.")],
    tool_schema: Annotated[
        bool, typer.Option(help="Print the agent-facing tool definition instead.")
    ] = False,
) -> None:
    """One capability in full, or its tool contract."""
    from cua.catalog.store import CapabilityStore

    store = CapabilityStore()
    try:
        capability = store.load(ref)
    except (LookupError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    if tool_schema:
        typer.echo(json.dumps(capability.tool_schema(), indent=2))
    else:
        typer.echo(dump_capability(capability))


@catalog_app.command("invoke")
def catalog_invoke(
    ref: Annotated[str, typer.Argument(help="Capability `id` or `id@version`.")],
    input: Annotated[  # noqa: A002 - reads naturally on the command line
        list[str] | None, typer.Option("--input", help="name=value, repeatable.")
    ] = None,
    base_url: Annotated[
        str | None, typer.Option(help="Bind {base_url} for this deployment.")
    ] = None,
    headless: Annotated[bool, typer.Option(help="Run the browser headless.")] = True,
) -> None:
    """Call a capability by name, the way an agent would.

    Identical execution to `cua replay` -- the only difference is that the capability is looked up
    and hash-verified rather than pointed at by path.
    """
    from cua.catalog.store import CapabilityStore

    store = CapabilityStore()
    try:
        capability = store.load(ref)
    except (LookupError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    supplied: dict[str, str] = {}
    for pair in input or []:
        if "=" not in pair:
            typer.secho(f"--input expects name=value, got {pair!r}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        name, _, value = pair.partition("=")
        supplied[name] = value

    result, run_dir = _replay_once(
        capability=capability,
        inputs=supplied,
        base_url=base_url,
        headless=headless,
        run_id=f"invoke-{uuid.uuid4().hex[:8]}",
    )
    typer.secho(f"\n{result.summary}")
    if result.outputs:
        typer.echo(f"  outputs  : {json.dumps(result.outputs, indent=2)}")
    typer.echo(f"  evidence : {run_dir}\n")
    raise typer.Exit(code=result.exit_code)


@app.command("agent-demo")
def agent_demo(
    base_url: Annotated[str, typer.Option(help="Where the target application is running.")],
    ref: Annotated[
        str, typer.Option(help="Capability to call.")
    ] = "corebank.member.savings_balance",
    member_id: Annotated[str, typer.Option(help="The argument to call it with.")] = "12345",
    headless: Annotated[bool, typer.Option(help="Run the browser headless.")] = True,
) -> None:
    """Call a capability the way an AI agent would: by name, with typed arguments.

    Start the target first with `make app`. Exit code follows the agent's disposition rather than
    the run's: 0 for an answer (including a negative one), 2 when a human now holds the session,
    1 for a defect.
    """
    from cua.cli.agent_demo import run_agent_demo

    raise typer.Exit(
        code=run_agent_demo(
            capability_ref=ref, member_id=member_id, base_url=base_url, headless=headless
        )
    )


@app.command()
def version() -> None:
    from cua import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
