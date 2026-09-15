"""A discovery run becomes a reviewable capability.

The hinge of the whole system: a wandering, probabilistic run in, a frozen contract out. These tests
run the real loop against the real app and compile what it produced -- so the artifact under
assertion was genuinely derived from a working run, not hand-built to satisfy the compiler.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from apps.mock_bank.server import VALID_PW, VALID_USER, create_app

from cua.agent.fake import FakeLlm, ScriptedCall
from cua.agent.loop import DiscoveryAgent
from cua.domain.approval import ApprovalState, CapabilityApproval
from cua.domain.discovery import DiscoveryRun, StopReason
from cua.domain.serde import dump_capability, load_capability
from cua.domain.values import InputRef
from cua.evidence.bus import EvidenceBus
from cua.policy.config import parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.redact import Redactor
from cua.recorder.compile import compile_capability
from cua.runtime.dispatcher import Dispatcher
from cua.surfaces.playwright_cdp.driver import PlaywrightCdpDriver
from cua.targeting.resolver import TargetResolver

pytestmark = [pytest.mark.browser, pytest.mark.slow]

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"
GOAL = "Look up member 12345 and read their current savings balance"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def app() -> Iterator[tuple[str, int]]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app("base"), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", port
    finally:
        server.should_exit = True
        thread.join(timeout=5)


SCRIPT = [
    ScriptedCall(
        "type_text",
        "enter the member number",
        role="textbox",
        name="Member Number",
        arguments={"text": "12345"},
    ),
    ScriptedCall("click", "run the search", role="button", name="Search"),
    ScriptedCall(
        "extract",
        "read the savings balance",
        role="cell",
        name="$4,210.55",
        occurrence=1,
        arguments={"output_name": "savings_balance"},
    ),
    ScriptedCall(
        "extract",
        "read the as-of date",
        role="cell",
        name="09/11/2026",
        arguments={"output_name": "as_of"},
    ),
    # The third cell reading "Active" -- the label/value summary table. The first two are rows of
    # the Accounts Overview grid, where the preceding cell is a *balance*; recording from there is
    # what produced `adjacent_to "$812.30"` in a committed artifact. Included here so the compiler
    # is exercised against a screen that can mislead it.
    ScriptedCall(
        "extract",
        "read the account status",
        role="cell",
        name="Active",
        occurrence=2,
        arguments={"output_name": "account_status"},
    ),
    ScriptedCall(
        "finish",
        "done",
        arguments={
            "summary": "Read member 12345's savings balance",
            "checkpoint": "Member Detail showing a currency-formatted savings balance",
        },
    ),
]


@pytest.fixture(scope="module")
def run(app: tuple[str, int], tmp_path_factory: pytest.TempPathFactory) -> DiscoveryRun:
    """One real discovery run, shared by the assertions below."""
    base_url, port = app
    config = parse_policy(POLICY.read_text())
    config = config.model_copy(
        update={
            "allowlist": config.allowlist.model_copy(
                update={"domains": (*config.allowlist.domains, f"127.0.0.1:{port}")}
            )
        }
    )
    redactor = Redactor(config.redaction)
    bus = EvidenceBus(tmp_path_factory.mktemp("compile"), redactor, run_id="compile-test")
    driver = PlaywrightCdpDriver(headless=True)
    try:
        page = driver.page
        page.goto(f"{base_url}/login")
        page.fill('input[name="user"]', VALID_USER)
        page.fill('input[name="pw"]', VALID_PW)
        page.click('input[type="submit"]')
        page.wait_for_load_state()
        page.goto(f"{base_url}/")
        page.wait_for_load_state()

        agent = DiscoveryAgent(
            llm=FakeLlm(script=list(SCRIPT)),
            dispatcher=Dispatcher(
                driver=driver,
                policy=PolicyEngine(config),
                resolver=TargetResolver(allow_vision=True),
                evidence=bus,
            ),
            evidence=bus,
            redactor=redactor,
        )
        result = agent.run(goal=GOAL, target_url=f"{base_url}/")
    finally:
        driver.close()

    assert result.succeeded, f"the discovery run failed: {result.stop_reason}"
    return result


@pytest.fixture(scope="module")
def compiled(run: DiscoveryRun):
    return compile_capability(
        run, vendor="acme-core", product="MemberDesk", entrypoint_url="{base_url}/"
    )


# -------------------------------------------------------------- the contract


def test_a_real_run_compiles_to_a_valid_capability(compiled) -> None:
    capability = compiled.capability
    assert capability.hash_is_valid()
    assert capability.steps, "a capability with no steps is not a capability"
    assert load_capability(dump_capability(capability)).ref == capability.ref


def test_the_literal_from_the_goal_becomes_a_parameter(compiled) -> None:
    """The whole point of compiling: the next caller supplies their own member number.

    A capability that replayed with 12345 baked in would return the same member's balance for every
    caller -- and would look entirely correct doing it.
    """
    capability = compiled.capability
    assert [spec.name for spec in capability.inputs] == ["member_number"]
    assert compiled.lifted_inputs == {"member_number": "12345"}

    typed = next(step for step in capability.steps if step.action.type == "type")
    assert isinstance(typed.action.value, InputRef)
    assert typed.action.value.input_name == "member_number"


def test_outputs_are_typed_from_what_was_observed(compiled) -> None:
    """A calling agent should not have to guess whether "$4,210.55" is a number."""
    by_name = {spec.name: spec for spec in compiled.capability.outputs}
    assert by_name["savings_balance"].type == "money"
    assert by_name["as_of"].type == "date"
    assert by_name["savings_balance"].source is not None


def test_targets_are_semantic_not_selectors(compiled) -> None:
    """AGENTS.md invariant 3, asserted on a compiled artifact rather than a hand-written one."""
    for step in compiled.capability.steps:
        target = getattr(step.action, "target", None)
        if target is None:
            continue
        assert target.role
        assert target.name or target.anchor or target.ordinal is not None
        assert target.recorded_strategy is not None, "the drift baseline must be recorded"


def test_only_durable_hints_are_persisted(compiled) -> None:
    """A CDP backend node id is valid only inside the session that observed it.

    Persisting one would put a handle in the artifact that can never resolve again -- and it would
    look like a fast path to anyone reading the YAML.
    """
    for step in compiled.capability.steps:
        target = getattr(step.action, "target", None)
        if target is None:
            continue
        assert "native" not in target.hints
        assert set(target.hints) <= {"css"}


def test_identifiers_are_typed_as_strings(compiled) -> None:
    """Leading zeros are meaningful in a banking identifier.

    Typing a member or account number as `integer` invites a caller -- or a JSON parser on the way
    in -- to drop the zeros that distinguish 0001234501 from 1234501.
    """
    member = next(spec for spec in compiled.capability.inputs if spec.name == "member_number")
    assert member.type == "string"
    assert member.pattern == r"^[0-9]{4,12}$"


def test_steps_carry_the_models_reasoning(compiled) -> None:
    """§3.5 asks for what the agent did *and why*. Carrying it into the artifact means a reviewer
    reads the intent instead of reverse-engineering it."""
    assert all(step.description for step in compiled.capability.steps)


# ------------------------------------------------------------- the checkpoint


def test_the_checkpoint_asserts_the_output_shape(compiled) -> None:
    """Not "a page loaded" -- a currency-formatted value beside its label.

    A checkpoint true of any page in the application verifies nothing while looking like
    verification.
    """
    described = compiled.capability.checkpoint.describe()
    assert "$" in described or "0-9" in described, described
    assert "Savings Balance" in described


def test_the_checkpoint_is_built_from_observation_not_the_models_hint(
    compiled, run: DiscoveryRun
) -> None:
    """The model's hint is preserved for a reviewer, but never executed.

    A self-report about a run that has already ended cannot be a machine-checkable claim about the
    screen in front of the executor.
    """
    assert run.checkpoint_hint
    assert run.checkpoint_hint not in compiled.capability.checkpoint.describe()


# ---------------------------------------------------- honesty about the gaps


def test_no_outcomes_or_recovery_are_invented(compiled) -> None:
    """The most important assertion in this file.

    A happy-path run never saw a "record not found" screen, so scaffolding a detector for one would
    declare error handling that was never observed to work. Confidently wrong error handling is
    worse than none: it makes a reviewer think the question has been answered.
    """
    assert compiled.capability.outcomes == ()
    assert compiled.capability.recovery == ()


def test_the_gaps_are_named_for_a_reviewer(compiled) -> None:
    """An honest gap is only useful if someone is told about it."""
    notes = compiled.capability.provenance.notes
    assert "outcomes" in notes.lower()
    assert "recovery" in notes.lower()
    assert "unexpected_state" in notes.lower(), "say what happens if the gaps are left unfilled"


def test_the_compiled_capability_is_a_draft(compiled) -> None:
    """Parameter lifting and checkpoint inference are heuristic. The approval gate exists to say
    so."""
    approval = CapabilityApproval(
        capability_ref=compiled.capability.ref,
        content_hash=compiled.capability.content_hash,
    )
    assert approval.state is ApprovalState.DRAFT
    assert not approval.permits_unattended_replay(content_hash=compiled.capability.content_hash)


def test_provenance_references_the_run_without_inlining_it(compiled, run: DiscoveryRun) -> None:
    """§3.2: the artifact is decoupled from the raw model transcript."""
    provenance = compiled.capability.provenance
    assert provenance.discovered_by is not None
    assert provenance.discovered_by.run_id == run.run_id
    assert provenance.discovered_by.model == run.model


# --------------------------------------------------------------- safety rails


def test_a_failed_run_is_refused(run: DiscoveryRun) -> None:
    """A capability is a claim that a flow works. A run that did not reach the goal is not evidence
    for that claim, and compiling one anyway would produce a confident artifact from a failure."""
    failed = DiscoveryRun(
        run_id="x", goal=GOAL, target_url="", stop_reason=StopReason.DEAD_END, steps=run.steps
    )
    with pytest.raises(ValueError, match="refusing to compile"):
        compile_capability(failed, vendor="v", product="p", entrypoint_url="{base_url}/")


def test_the_capability_is_namespaced_by_product_not_tenant(compiled) -> None:
    """ADR 0005: the precondition for cross-tenant reuse. An id carrying an institution's name
    could never be shared with the next one running the same software."""
    assert compiled.capability.id.startswith("memberdesk.")
    assert compiled.capability.surface.app.product == "MemberDesk"


def test_the_compiled_capability_replays_for_a_different_member(
    compiled, app: tuple[str, int], tmp_path: Path
) -> None:
    """The through-line, end to end: discover once, replay for someone else.

    Every other test here inspects the artifact's *shape*. This one executes it, against a member
    the discovery run never saw, and is the only test that can catch the failure that matters --
    a capability that compiles, validates, serializes, and then works for exactly one record.

    It has caught one already. The compiler described the account-status cell by the text beside
    it, which on the Accounts Overview grid is a balance rather than a label, so the artifact
    carried `adjacent_to "$812.30"` -- member 12345's checking balance -- and resolved for nobody
    else. Shape assertions all passed; only replaying it for 67890 revealed it.
    """
    from cua.domain.result import RunStatus
    from cua.domain.tenant_binding import TenantBinding
    from cua.replay.executor import ReplayExecutor

    base_url, port = app
    config = parse_policy(POLICY.read_text())
    config = config.model_copy(
        update={
            "allowlist": config.allowlist.model_copy(
                update={"domains": (*config.allowlist.domains, f"127.0.0.1:{port}")}
            )
        }
    )
    redactor = Redactor(config.redaction)
    bus = EvidenceBus(tmp_path, redactor, run_id="compiled-replay")

    bound = TenantBinding.model_validate(
        {
            "capability_ref": compiled.capability.ref,
            "tenant": "test",
            "vars": {"base_url": base_url},
        }
    ).apply(compiled.capability)

    driver = PlaywrightCdpDriver(headless=True)
    try:
        page = driver.page
        page.goto(f"{base_url}/login")
        page.fill('input[name="user"]', VALID_USER)
        page.fill('input[name="pw"]', VALID_PW)
        page.click('input[type="submit"]')
        page.wait_for_load_state()

        result = ReplayExecutor(
            dispatcher=Dispatcher(
                driver=driver,
                policy=PolicyEngine(config),
                resolver=TargetResolver(allow_vision=False),
                evidence=bus,
            ),
            evidence=bus,
        ).run(bound, {"member_number": "67890"})
    finally:
        driver.close()

    assert result.status is RunStatus.SUCCESS, result.summary
    assert result.outputs["savings_balance"] == "$18,730.00", result.outputs
    assert result.outputs["account_status"] == "Active", result.outputs


def test_no_target_is_described_by_a_value_it_observed(compiled) -> None:
    """A recorded anchor must be a label, never a datum.

    The cheap, fast guard for the same defect: an anchor carrying a currency amount, a date or a
    long number has captured one record's data as though it were page structure.
    """
    from cua.recorder.naming import looks_like_value

    for step in compiled.capability.steps:
        target = getattr(step.action, "target", None)
        if target is None or target.anchor is None:
            continue
        assert not looks_like_value(target.anchor.text), (
            f"step {step.id!r} is anchored to {target.anchor.text!r}, which is a value, not a label"
        )


def test_the_discovery_host_does_not_survive_into_the_artifact(run: DiscoveryRun) -> None:
    """An artifact that names its origin is bound to the machine it was recorded on.

    `cua discover` is driven against one concrete host, and the compiler used to copy that host
    into `entrypoint.url_pattern` verbatim. Replay then had no `{base_url}` to substitute, so
    `--base-url` was silently ignored: the run signed in to the host the caller asked for, then
    navigated to the host baked into the artifact, arrived at a sign-on screen with no session,
    and failed its first precondition. The error named the resolver, which was not at fault.

    It also made the portability claim untrue -- a `TenantBinding` exists precisely so one
    artifact can run against another deployment, and it cannot if the origin is a literal.
    """
    compiled = compile_capability(
        run, vendor="acme-core", product="MemberDesk", entrypoint_url="http://127.0.0.1:8811"
    )
    pattern = compiled.capability.entrypoint.url_pattern
    assert pattern.startswith("{base_url}"), pattern
    assert "127.0.0.1" not in pattern and "8811" not in pattern

    # The path is a property of the capability, not of the deployment, so it is kept.
    deeper = compile_capability(
        run, vendor="acme-core", product="MemberDesk", entrypoint_url="http://host:9/app/search"
    )
    assert deeper.capability.entrypoint.url_pattern == "{base_url}/app/search"

    # Idempotent: `cua demo` already passes the placeholder and must get it back unchanged.
    already = compile_capability(
        run, vendor="acme-core", product="MemberDesk", entrypoint_url="{base_url}/"
    )
    assert already.capability.entrypoint.url_pattern == "{base_url}/"
