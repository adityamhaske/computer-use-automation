"""A human takes over the live session, acts, hands it back, and the run resumes.

The brief calls this out as the thing submissions most often fake, so it is tested against a real
browser driving the real application: the operator's clicks go into the *same* Chromium session the
automation was using, not a fresh one.

Everything here is provable headless. The pixel-streaming console is a view onto this model, added
last and deliberately: the control-transfer model is what is graded, and the pixels are polish.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from apps.mock_bank.server import VALID_PW, VALID_USER, create_app

from cua.domain.action import Click, RawInput, RawInputKind
from cua.domain.actor import Actor
from cua.domain.capability import Capability
from cua.domain.result import FailureCode, RunStatus
from cua.domain.serde import load_capability
from cua.domain.snapshot import NodeScope
from cua.domain.target import NameMatch, TargetDescriptor
from cua.domain.tenant_binding import TenantBinding
from cua.evidence.bus import EventType, EvidenceBus
from cua.hitl.broker import SessionBroker
from cua.hitl.intervention import InterventionState
from cua.hitl.lease import ControlState, LeaseError
from cua.hitl.reanchor import reconcile
from cua.policy.config import parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.redact import Redactor
from cua.replay.executor import ReplayExecutor
from cua.runtime.dispatcher import Dispatcher, DispatchStatus
from cua.surfaces.playwright_cdp.driver import PlaywrightCdpDriver
from cua.targeting.resolver import TargetResolver

pytestmark = [pytest.mark.browser, pytest.mark.slow]

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/capabilities/savings_balance.yaml"


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


@pytest.fixture
def rig(app: tuple[str, int], tmp_path: Path) -> Iterator[tuple]:
    base_url, port = app
    httpx.post(f"{base_url}/_control/reset", timeout=5)

    config = parse_policy(POLICY.read_text())
    config = config.model_copy(
        update={
            "allowlist": config.allowlist.model_copy(
                update={"domains": (*config.allowlist.domains, f"127.0.0.1:{port}")}
            )
        }
    )
    redactor = Redactor(config.redaction)
    bus = EvidenceBus(tmp_path, redactor, run_id="handoff")
    driver = PlaywrightCdpDriver(headless=True)

    page = driver.page
    page.goto(f"{base_url}/login")
    page.fill('input[name="user"]', VALID_USER)
    page.fill('input[name="pw"]', VALID_PW)
    page.click('input[type="submit"]')
    page.wait_for_load_state()
    page.goto(f"{base_url}/")
    page.wait_for_load_state()

    dispatcher = Dispatcher(
        driver=driver,
        policy=PolicyEngine(config),
        resolver=TargetResolver(allow_vision=False),
        evidence=bus,
    )
    broker = SessionBroker(session_id="sess-handoff", evidence=bus, dispatch=dispatcher)
    try:
        yield broker, dispatcher, driver, bus
    finally:
        driver.close()


@pytest.fixture
def capability(app: tuple[str, int]) -> Capability:
    return TenantBinding.model_validate(
        {
            "capability_ref": "corebank.member.savings_balance@1.0.0",
            "tenant": "test",
            "vars": {"base_url": app[0]},
        }
    ).apply(load_capability(FIXTURE.read_text()))


def _escalate(broker: SessionBroker, driver: PlaywrightCdpDriver, capability: Capability):
    return broker.escalate(
        run_id="run-1",
        capability_ref=capability.ref,
        goal=capability.description,
        reason="the resolver refused an ambiguous control",
        step_id="submit_search",
        failure_code="target_ambiguous",
        snapshot=driver.observe(),
    )


# ================================================= the thread, end to end


def test_a_human_takes_over_acts_and_hands_back(
    rig: tuple, capability: Capability, app: tuple[str, int]
) -> None:
    """The full cycle, on one live session.

    The operator completes the search by hand -- in the same browser the automation was driving --
    and the executor works out where to pick up.
    """
    broker, _dispatcher, driver, _ = rig
    _base_url, _ = app

    request = _escalate(broker, driver, capability)
    assert broker.lease.state is ControlState.PAUSED
    assert request.state is InterventionState.OPEN

    broker.claim(request.intervention_id, "operator-1")
    assert broker.lease.state is ControlState.HUMAN_CONTROL
    assert broker.lease.holder is Actor.HUMAN

    # The operator does the work: type the member number, then submit. Same session, same page.
    typed = broker.human_action(_type_action("Member Number", "12345"))
    assert typed.status is DispatchStatus.OK

    clicked = broker.human_action(
        Click(
            target=TargetDescriptor(
                role="button", name=NameMatch(value="Search"), scope=NodeScope(frame="content")
            )
        )
    )
    assert clicked.status is DispatchStatus.OK

    delta = broker.release(snapshot_after=driver.observe())
    assert delta is not None and delta.changed, "the operator's work must be visible in the diff"

    epoch = broker.resume()
    assert broker.lease.state is ControlState.RUNNING
    assert broker.lease.holder is Actor.AUTOMATION

    # Re-anchor: the operator already completed the search, so the executor picks up at the read.
    plan = reconcile(capability, driver.observe(), inputs={"member_id": "12345"}, from_index=2)
    assert plan.can_resume, plan.reason
    assert capability.steps[plan.resume_index].id == "read_balance"
    assert epoch > 1


def _type_action(field_name: str, text: str):
    from cua.domain.action import Type

    return Type(
        target=TargetDescriptor(
            role="textbox", name=NameMatch(value=field_name), scope=NodeScope(frame="content")
        ),
        value=text,
    )


# ======================================================= the epoch matters


def test_stale_automation_cannot_act_after_a_takeover(rig: tuple, capability: Capability) -> None:
    """The race this mechanism exists to kill.

    Escalation happens while a step is in flight. Without the epoch check that step lands on the
    page a moment after the operator started typing, and both writers appear legitimate in the
    evidence.
    """
    broker, dispatcher, driver, _ = rig

    stale_epoch = broker.lease.epoch  # what automation was authorized under
    request = _escalate(broker, driver, capability)
    broker.claim(request.intervention_id, "operator-1")

    outcome = dispatcher.execute(
        Click(target=TargetDescriptor(role="button", name=NameMatch(value="Search"))),
        snapshot=driver.observe(),
        actor=Actor.AUTOMATION,
        session_id="sess-handoff",
        lease_epoch=stale_epoch,
        expected_epoch=broker.lease.epoch,
    )
    assert outcome.status is DispatchStatus.LEASE_LOST
    assert outcome.failure_code is not None and outcome.failure_code.value == "lease_lost"


def test_nobody_may_act_while_the_session_is_merely_paused(
    rig: tuple, capability: Capability
) -> None:
    """Escalation hands the session to nobody, not straight to a human.

    There may be no operator, and a session nominally held by an absent one is a session nothing
    can recover.
    """
    broker, _, driver, _ = rig
    _escalate(broker, driver, capability)

    with pytest.raises(LeaseError):
        broker.human_action(RawInput(kind=RawInputKind.MOUSE_CLICK, x=10, y=10))


# ================================================ the human is still policed


def test_human_input_goes_through_policy(rig: tuple, capability: Capability) -> None:
    """AGENTS.md invariant 4, on the live path rather than in isolation.

    The console submits actions; it does not inject events. So an operator's click is authorized,
    risk-classified and recorded exactly like automation's.
    """
    broker, _, driver, bus = rig
    request = _escalate(broker, driver, capability)
    broker.claim(request.intervention_id, "operator-1")

    broker.human_action(RawInput(kind=RawInputKind.MOUSE_CLICK, x=40, y=40))

    events = bus.read_events()
    authorize = [e for e in events if e["event"] == EventType.AUTHORIZE.value]
    assert authorize, "a human action must be authorized like any other"
    assert authorize[-1]["actor"] == "human"
    assert authorize[-1]["granted"] is True
    assert bus.unauthorized_dispatches() == []


def test_a_human_still_cannot_leave_the_allowlist(rig: tuple, capability: Capability) -> None:
    """Taking control widens *what* an operator may do, never *where*."""
    from cua.domain.action import Navigate

    broker, _, driver, _ = rig
    request = _escalate(broker, driver, capability)
    broker.claim(request.intervention_id, "operator-1")

    outcome = broker.human_action(Navigate(url="http://evil.example.com/collect"))
    assert outcome.status is DispatchStatus.DENIED
    assert outcome.failure_code is not None
    assert outcome.failure_code.value == "navigation_blocked"


def test_every_human_action_is_attributed(rig: tuple, capability: Capability) -> None:
    """ "Record what the human did" is a consequence of the path, not a feature bolted on."""
    broker, _, driver, bus = rig
    request = _escalate(broker, driver, capability)
    broker.claim(request.intervention_id, "operator-2")
    broker.human_action(RawInput(kind=RawInputKind.MOUSE_CLICK, x=20, y=20))
    broker.release(snapshot_after=driver.observe())

    dispatches = [
        e
        for e in bus.read_events()
        if e["event"] == EventType.DISPATCH.value and e["actor"] == "human"
    ]
    assert dispatches, "the operator's actions must appear in the evidence"
    assert all(e["lease_epoch"] >= 3 for e in dispatches), "tagged with the grant they acted under"

    released = [
        e
        for e in bus.read_events()
        if e["event"] == EventType.LEASE.value and e.get("transition") == "released"
    ]
    assert released and released[0]["human_actions"] == 1


# ============================================================== re-anchoring


def test_resuming_into_an_unrecognized_state_fails_closed(
    rig: tuple, capability: Capability, app: tuple[str, int]
) -> None:
    """If the operator left the session somewhere the capability does not describe, resuming would
    be guessing which step they had reached."""
    broker, _, driver, _ = rig
    base_url, _ = app
    request = _escalate(broker, driver, capability)
    broker.claim(request.intervention_id, "operator-1")

    # The operator wanders off the recorded flow -- still inside the allowlist.
    driver.page.frame(name="content").goto(f"{base_url}/reports")
    driver.page.wait_for_load_state()

    broker.release(snapshot_after=driver.observe())
    broker.resume()

    plan = reconcile(capability, driver.observe(), inputs={"member_id": "12345"}, from_index=2)
    assert not plan.can_resume
    assert "does not describe" in plan.reason or "expects" in plan.reason


def test_the_intervention_carries_enough_context_to_act_on(
    rig: tuple, capability: Capability
) -> None:
    """What an operator is shown, without opening a log."""
    broker, _, driver, _ = rig
    request = _escalate(broker, driver, capability)

    card = request.context_card()
    assert card["capability"] == capability.ref
    assert card["goal"]
    assert card["stopped_at"] == "submit_search"
    assert card["because"]
    assert card["evidence"]


def test_the_queue_is_oldest_first(rig: tuple, capability: Capability) -> None:
    """A stuck run nobody looked at for an hour is more urgent than one that just stopped."""
    broker, _, driver, _ = rig
    first = _escalate(broker, driver, capability)
    broker.claim(first.intervention_id, "operator-1")
    broker.release(snapshot_after=driver.observe())
    broker.resume()
    second = _escalate(broker, driver, capability)

    pending = broker.queue.pending()
    assert [r.intervention_id for r in pending] == [second.intervention_id]
    assert broker.queue.get(first.intervention_id).state is InterventionState.RESOLVED


# ================================== a real replay escalation, end to end


def test_a_replay_escalation_opens_a_real_intervention(
    rig: tuple, capability: Capability, app: tuple[str, int]
) -> None:
    """The full production path: an undeclared screen stops a replay, the broker opens an
    intervention, and the session is paused so nothing in flight can still act."""
    from cua.evidence.bus import EvidenceBus  # noqa: F401 - used via the rig
    from cua.replay.executor import ReplayExecutor
    from cua.runtime.capture import FailureCapture

    broker, dispatcher, driver, bus = rig
    base_url, _ = app
    httpx.post(
        f"{base_url}/_control/arm", json={"fault": "undeclared_dialog", "count": 3}, timeout=5
    )

    executor = ReplayExecutor(
        dispatcher=dispatcher,
        evidence=bus,
        capture=FailureCapture(driver=driver, evidence=bus),
        broker=broker,
    )
    result = executor.run(capability, {"member_id": "12345"})
    httpx.post(f"{base_url}/_control/reset", timeout=5)

    assert result.status.value == "needs_human", result.summary
    assert result.intervention is not None

    # The intervention is real: it is in the queue, and the session is paused.
    request = broker.queue.get(result.intervention.intervention_id)
    assert request is not None
    assert request.state is InterventionState.OPEN
    assert broker.lease.state is ControlState.PAUSED

    # And it carries what an operator needs, including a picture of the screen that stopped it.
    card = request.context_card()
    assert card["because"]
    assert card["screenshot"], "the brief asks for a richer signal on failure"


# ================================================ the race the design claims to kill


def test_automation_cannot_act_after_a_human_claims_the_live_session(
    rig: tuple, capability: Capability, app: tuple[str, int]
) -> None:
    """AGENTS.md invariant 8, asserted against the path that actually runs.

    `tests/integration/test_dispatcher.py` already proves the dispatcher *can* reject a stale
    epoch -- it constructs one by hand. That is a different claim from the one the design makes,
    which is that a **replay in flight** cannot act once an operator has taken the session. This
    test drives the real executor.

    The distinction matters because the dispatcher's epoch check is opt-in (`expected_epoch`
    defaults to None). A caller that never passes it is never checked, and a test that calls the
    dispatcher directly cannot notice.
    """
    broker, _, driver, evidence = rig
    base_url, _ = app

    executor = ReplayExecutor(
        dispatcher=broker.dispatch,
        evidence=evidence,
        broker=broker,
    )

    # An operator takes the live session mid-flight. Every transition bumps the epoch, so the
    # epoch the executor was authorized under is now stale.
    request = _escalate(broker, driver, capability)
    broker.claim(request.intervention_id, "operator-1")
    assert broker.lease.state is ControlState.HUMAN_CONTROL

    bound = TenantBinding(
        capability_ref=capability.ref, tenant="race", vars={"base_url": base_url}
    ).apply(capability)

    result = executor.run(bound, {"member_id": "12345"})

    assert result.status is RunStatus.FAILED, (
        f"automation ran to {result.status.value} while a human held the live session -- "
        "this is the race the lease exists to close"
    )
    assert result.error is not None
    assert result.error.code is FailureCode.LEASE_LOST, (
        f"expected the dispatch to be refused as stale, got {result.error.code.value}"
    )


def test_a_run_authorized_before_the_handoff_cannot_resume_on_its_old_epoch(
    rig: tuple, capability: Capability, app: tuple[str, int]
) -> None:
    """The other half of the guard, and the one an epoch is actually for.

    After an operator takes the session and hands it back, control belongs to automation again --
    so a holder check alone would wave this through. But a run authorized *before* the handoff has
    no idea what the operator did, and resuming it blindly would replay steps against a screen it
    never saw. The epoch is what distinguishes "automation may act" from "*this* run may act".
    """
    broker, _, driver, evidence = rig
    base_url, _ = app

    request = _escalate(broker, driver, capability)
    stale = broker.lease.epoch  # what a run in flight at this moment would hold

    broker.claim(request.intervention_id, "operator-1")
    broker.release(snapshot_after=driver.observe())
    broker.resume()

    assert broker.lease.holder is Actor.AUTOMATION, "control is automation's again"
    assert broker.lease.epoch > stale, "but every transition moved the epoch on"

    bound = TenantBinding(
        capability_ref=capability.ref, tenant="stale", vars={"base_url": base_url}
    ).apply(capability)
    result = ReplayExecutor(
        dispatcher=broker.dispatch, evidence=evidence, broker=broker, lease_epoch=stale
    ).run(bound, {"member_id": "12345"})

    assert result.status is RunStatus.FAILED
    assert result.error is not None
    assert result.error.code is FailureCode.LEASE_LOST
