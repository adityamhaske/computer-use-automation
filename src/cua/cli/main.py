"""Command line entry points."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from dotenv import load_dotenv

from cua.agent.llm import LlmError, OpenRouterLlm
from cua.agent.loop import DiscoveryAgent
from cua.agent.stop import Budget
from cua.domain.capability import Capability
from cua.domain.result import RunResult, RunStatus
from cua.domain.serde import dump_capability
from cua.recorder.compile import compile_capability
from cua.runtime.wiring import build_rig

app = typer.Typer(
    add_completion=False,
    help="Computer-use automation: discover once with an LLM, replay deterministically.",
)


@app.callback()
def _bootstrap() -> None:
    """Load `.env` before any command runs.

    `OpenRouterLlm` reads its key through a `default_factory`, so the environment is consulted when
    the client is *constructed*, not when this module is imported -- which is why loading here, in a
    Typer callback that runs before every subcommand, is enough. Without it `.env` was documented in
    the README and read by nothing, and `make demo` silently fell back to the recorded transcript
    while a perfectly good key sat in the file.
    """
    load_dotenv()


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
        rig.dispatcher.open_entrypoint(target, session_id=run_id)
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
    sign_in: Annotated[
        bool,
        typer.Option(
            "--sign-in",
            help=(
                "Sign in to the mock back-office before replaying. This capability's entrypoint is "
                "the app's authenticated frameset, not the bare sign-on page -- a freshly booted "
                "`make app` has no session, so without this the run correctly fails closed at its "
                "first precondition (AGENTS.md invariant 5) instead of guessing. Demo-fixture "
                "setup only: fixed mock credentials, drives the browser directly, does not go "
                "through policy (AGENTS.md invariant 2) -- see cua.cli.mock_login. "
                "Requires --base-url."
            ),
        ),
    ] = False,
) -> None:
    """Execute a saved capability deterministically. No model is involved."""
    from cua.domain.serde import load_capability
    from cua.domain.tenant_binding import TenantBinding

    if sign_in and not base_url:
        typer.secho(
            "--sign-in requires --base-url (where to sign in).", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)

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
        # NOT base_url=base_url here: the binding above already applied it to `capability`, and
        # _replay_once would apply it a second time given the chance. sign_in_url is a separate,
        # narrower parameter that only ever drives the browser to a login page -- it never touches
        # TenantBinding, so it carries no risk of a double-apply.
        sign_in=sign_in,
        sign_in_url=base_url,
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
    sign_in: bool = False,
    sign_in_url: str | None = None,
) -> tuple[RunResult, Path]:
    """Execute one capability and return its result and evidence directory.

    Shared by `cua replay` and the calling-agent demo so the two cannot drift apart. A demo that
    took a different path to the executor would be demonstrating something other than what the CLI
    does, which is the failure mode of most "example" code.

    `sign_in_url` is deliberately independent of `base_url`: the latter drives `TenantBinding`
    (applied here, or already applied by a caller that pre-bound the capability itself -- applying
    it twice would be a bug, not a no-op, so this function must never assume it owns that step).
    Signing in is not tenant binding -- it is demo-fixture setup that happens to need a URL, and
    every caller already has one in scope regardless of who applied the binding.
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
        if sign_in:
            assert sign_in_url is not None, "sign_in requires sign_in_url"
            from cua.cli.mock_login import sign_in as sign_in_to_mock_app

            sign_in_to_mock_app(rig, sign_in_url)
        result = ReplayExecutor(
            dispatcher=rig.dispatcher,
            evidence=rig.evidence,
            capture=FailureCapture(driver=rig.driver, evidence=rig.evidence),
            allow_irreversible=allow_irreversible,
            # From the policy file rather than the dataclass defaults, so `budgets` and
            # `replay_gates` in config/policy.yaml are settings rather than documentation.
            max_recovery_attempts_total=rig.config.budgets.max_recovery_attempts_total,
            replay_gates=rig.config.replay_gates,
        ).run(capability, inputs)
    finally:
        rig.close()
    return result, rig.run_dir


def _parse_inputs(pairs: list[str] | None) -> dict[str, str]:
    """`--input name=value`, repeated, into a dict."""
    supplied: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            typer.secho(f"--input expects name=value, got {pair!r}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        name, _, value = pair.partition("=")
        supplied[name] = value
    return supplied


def _supervised_run(
    supervised: Any,
    *,
    capability: str,
    base_url: str,
    inputs: dict[str, str],
    arm_fault: str | None,
    sign_in: bool,
) -> Capability:
    """Run a capability on the console's own session, so an escalation lands in its queue.

    Everything here goes through `supervised.session.call`. Synchronous Playwright is bound to the
    thread that created it, and the console serves requests from a threadpool -- driving the surface
    from this thread would fail on the first action with a greenlet error.

    The run happens before the server starts rather than beside it: it escalates within a second or
    two, and a queue that is already populated when the first page loads is a better demonstration
    than one that appears while the reviewer is reading an empty state.
    """
    from cua.catalog.store import CapabilityStore
    from cua.domain.serde import load_capability
    from cua.domain.tenant_binding import TenantBinding
    from cua.policy.config import ReplayGates
    from cua.replay.executor import ReplayExecutor
    from cua.runtime.capture import FailureCapture

    path = Path(capability)
    loaded = (
        load_capability(path.read_text(encoding="utf-8"))
        if path.suffix in {".yaml", ".yml"} and path.exists()
        else CapabilityStore().load(capability)
    )
    bound = TenantBinding(
        capability_ref=loaded.ref, tenant="console", vars={"base_url": base_url}
    ).apply(loaded)

    if sign_in:
        from cua.cli.mock_login import sign_in as do_sign_in

        supervised.session.call(
            lambda: do_sign_in(_BorrowedRig(supervised.driver), base_url)  # type: ignore[arg-type]
        )

    if arm_fault:
        import httpx

        httpx.post(f"{base_url}/_control/arm", json={"fault": arm_fault, "count": 4}, timeout=5)
        typer.echo(f"  armed       : {arm_fault}")

    executor = ReplayExecutor(
        dispatcher=supervised.dispatcher,
        evidence=supervised.evidence,
        capture=FailureCapture(driver=supervised.driver, evidence=supervised.evidence),
        broker=supervised.broker,
        max_recovery_attempts_total=supervised.config.budgets.max_recovery_attempts_total
        if supervised.config
        else 6,
        replay_gates=supervised.config.replay_gates if supervised.config else ReplayGates(),
    )
    result = supervised.session.call(lambda: executor.run(bound, inputs))
    if arm_fault:
        import httpx

        httpx.post(f"{base_url}/_control/reset", timeout=5)

    typer.echo(f"  supervised  : {bound.ref} -> {result.summary}")
    if result.status is RunStatus.NEEDS_HUMAN:
        typer.secho(
            "  An intervention is waiting. Open the console and claim it.",
            fg=typer.colors.YELLOW,
        )
    return bound


@dataclass
class _BorrowedRig:
    """Just enough of a `Rig` for `mock_login.sign_in`, which only wants the driver."""

    driver: Any


@app.command()
def console(
    port: Annotated[int, typer.Option(help="Port for the operator console.")] = 8812,
    target: Annotated[str, typer.Option(help="URL to open in the supervised session.")] = (
        "http://localhost:8811/"
    ),
    headless: Annotated[bool, typer.Option(help="Run the supervised browser headless.")] = True,
    capability: Annotated[
        str | None,
        typer.Option(
            help=(
                "Run this capability against the supervised session before serving, so the console "
                "opens with something to act on. A ref from the catalog, or a path to a YAML."
            )
        ),
    ] = None,
    arm_fault: Annotated[
        str | None,
        typer.Option(
            help=(
                "Arm a fault on the mock app first (e.g. undeclared_dialog) so the run escalates "
                "and an intervention is waiting. Requires --capability."
            )
        ),
    ] = None,
    input: Annotated[  # noqa: A002 - reads naturally on the command line
        list[str] | None, typer.Option("--input", help="name=value for --capability, repeatable.")
    ] = None,
    sign_in: Annotated[
        bool, typer.Option("--sign-in", help="Sign in to the mock app first. See `cua replay`.")
    ] = False,
) -> None:
    """Run the operator console over a supervised live session.

    Single operator, no authentication, local only -- a documented cut (REPORT.md §5). What is real:
    the operator drives the same Chromium session automation uses, their input travels the same
    policy chokepoint, and every action is recorded with actor=HUMAN.

    With `--capability` the console does not just *watch* a session, it supervises a real run on it.
    Without that, nothing on the session can ever escalate, so the intervention queue stays empty
    and the one flow this console exists for is unreachable from the served UI -- the handoff only
    ever happened inside `cua demo`, headless, as printed text.
    """
    import uvicorn

    from cua.hitl.console.server import ConsoleDeps, create_console
    from cua.runtime.wiring import build_supervised_session

    if arm_fault and not capability:
        typer.secho("--arm-fault needs --capability to run.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    run_id = f"con-{uuid.uuid4().hex[:10]}"
    supervised = build_supervised_session(run_id=run_id, target=target, headless=headless)

    typer.secho(f"Operator console on http://127.0.0.1:{port}", fg=typer.colors.GREEN)
    typer.echo(f"  supervising : {target}")
    typer.echo(f"  evidence    : {supervised.run_dir}")

    supervising: Capability | None = None
    if capability:
        supervising = _supervised_run(
            supervised,
            capability=capability,
            base_url=target.rstrip("/"),
            inputs=_parse_inputs(input),
            arm_fault=arm_fault,
            sign_in=sign_in,
        )
    try:
        uvicorn.run(
            create_console(
                ConsoleDeps(
                    broker=supervised.broker,
                    driver=supervised.driver,
                    session=supervised.session,
                    # So the live view can honour the capability's `sensitive` declarations.
                    supervising=supervising,
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
    sign_in: Annotated[
        bool,
        typer.Option(
            "--sign-in",
            help=(
                "Sign in to the mock back-office first -- see `cua replay --help` for why a fresh "
                "`make app` needs this. Demo-fixture setup only; requires --base-url."
            ),
        ),
    ] = False,
) -> None:
    """Call a capability by name, the way an agent would.

    Identical execution to `cua replay` -- the only difference is that the capability is looked up
    and hash-verified rather than pointed at by path.
    """
    from cua.catalog.store import CapabilityStore

    if sign_in and not base_url:
        typer.secho(
            "--sign-in requires --base-url (where to sign in).", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)

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
        sign_in=sign_in,
        sign_in_url=base_url,
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
    sign_in: Annotated[
        bool,
        typer.Option(
            "--sign-in",
            help=(
                "Sign in to the mock back-office first -- see `cua replay --help` for why a fresh "
                "`make app` needs this. Demo-fixture setup only."
            ),
        ),
    ] = False,
) -> None:
    """Call a capability the way an AI agent would: by name, with typed arguments.

    Start the target first with `make app`. Exit code follows the agent's disposition rather than
    the run's: 0 for an answer (including a negative one), 2 when a human now holds the session,
    1 for a defect.
    """
    from cua.cli.agent_demo import run_agent_demo

    raise typer.Exit(
        code=run_agent_demo(
            capability_ref=ref,
            member_id=member_id,
            base_url=base_url,
            headless=headless,
            sign_in=sign_in,
        )
    )


@app.command()
def version() -> None:
    from cua import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
