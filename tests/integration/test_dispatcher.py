"""The dispatcher enforces the sequence, and the evidence proves it did.

Driven against a fake driver rather than a browser: the thing under test is the *order of
operations* and what lands in the evidence trail, neither of which needs Chromium. The real driver
is covered by the browser-backed perception tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cua.domain.action import Click, Navigate, Type
from cua.domain.actor import Actor
from cua.domain.result import FailureCode
from cua.domain.snapshot import NodeScope, UiNode, UiSnapshot
from cua.domain.target import NameMatch, TargetDescriptor
from cua.evidence.bus import EventType, EvidenceBus
from cua.policy.authorized import AuthorizedAction
from cua.policy.config import parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.redact import Redactor
from cua.runtime.dispatcher import Dispatcher, DispatchStatus
from cua.surfaces.base import ActionResult, SessionInfo
from cua.targeting.resolver import TargetResolver

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"


class FakeDriver:
    """Records what it was asked to do, and proves it was asked correctly.

    Crucially it asserts its own precondition: `dispatch` must receive an `AuthorizedAction`. A test
    double that accepted anything would let the chokepoint rot while the suite stayed green.
    """

    def __init__(self, snapshot: UiSnapshot, *, lands_on: str | None = None) -> None:
        self._snapshot = snapshot
        self.dispatched: list[AuthorizedAction] = []
        self._lands_on = lands_on
        """Where the session ends up after a dispatch, when the action navigates.

        Set it to model the case the allowlist used to miss entirely: a *click* on a link that
        leaves the permitted host. The action carries no URL, so nothing could be checked up front.
        """

    def observe(self) -> UiSnapshot:
        return self._snapshot

    def dispatch(self, action: AuthorizedAction) -> ActionResult:
        assert isinstance(action, AuthorizedAction), "the driver must only ever see authorized work"
        self.dispatched.append(action)
        return ActionResult(ok=True, duration_ms=3, navigated=self._lands_on is not None)

    def screenshot(self, *, redact: tuple = ()) -> bytes:
        return b"\x89PNG"

    def bounds_for(self, node_ids: tuple[str, ...]) -> dict:
        return {}

    def session_info(self) -> SessionInfo:
        return SessionInfo(
            session_id="sess-1",
            driver="fake",
            capabilities=("semantic_tree",),
            url=self._lands_on or "http://127.0.0.1:8811/search",
        )

    def close(self) -> None:
        return None


def _page() -> UiSnapshot:
    return UiSnapshot(
        snapshot_id="s1",
        url="http://localhost:8811/search",
        nodes=(
            UiNode(
                node_id="n1", role="textbox", name="Member Number", scope=NodeScope(frame="content")
            ),
            UiNode(node_id="n2", role="button", name="Search", scope=NodeScope(frame="content")),
            UiNode(
                node_id="n3", role="button", name="Transfer Funds", scope=NodeScope(frame="content")
            ),
            UiNode(node_id="n4", role="button", name="Submit", scope=NodeScope(frame="content")),
            UiNode(node_id="n5", role="button", name="Submit", scope=NodeScope(frame="content")),
        ),
    )


@pytest.fixture
def rig(tmp_path: Path) -> tuple[Dispatcher, FakeDriver, EvidenceBus]:
    config = parse_policy(POLICY.read_text())
    bus = EvidenceBus(tmp_path, Redactor(config.redaction), run_id="run-1")
    driver = FakeDriver(_page())
    dispatcher = Dispatcher(
        driver=driver, policy=PolicyEngine(config), resolver=TargetResolver(), evidence=bus
    )
    return dispatcher, driver, bus


SESSION = {"actor": Actor.AUTOMATION, "session_id": "sess-1", "lease_epoch": 1}


def _click(name: str) -> Click:
    return Click(
        target=TargetDescriptor(
            role="button", name=NameMatch(value=name), scope=NodeScope(frame="content")
        )
    )


# ------------------------------------------------------------- happy path


def test_full_sequence_is_recorded(rig) -> None:
    dispatcher, driver, bus = rig
    outcome = dispatcher.execute(_click("Search"), snapshot=_page(), **SESSION)

    assert outcome.status is DispatchStatus.OK
    assert len(driver.dispatched) == 1
    # The driver acts on the node the resolver chose, not on a re-query. Re-querying would
    # reintroduce selector brittleness and open a window where the page changes in between.
    assert driver.dispatched[0].resolved_node_id == "n2"

    events = [event["event"] for event in bus.read_events()]
    assert events == [
        EventType.RESOLVE.value,
        EventType.AUTHORIZE.value,
        EventType.DISPATCH.value,
    ], "resolve, then authorize knowing the target, then dispatch"


def test_every_dispatch_has_a_matching_authorization(rig) -> None:
    """The reconciliation that makes the chokepoint checkable from the artifact a run leaves
    behind -- not from trusting the code that produced it."""
    dispatcher, _, bus = rig
    page = _page()
    dispatcher.execute(_click("Search"), snapshot=page, **SESSION)
    dispatcher.execute(
        Type(
            target=TargetDescriptor(role="textbox", name=NameMatch(value="Member Number")),
            value="12345",
        ),
        snapshot=page,
        **SESSION,
    )

    assert bus.unauthorized_dispatches() == []


# ----------------------------------------------------------- refusal paths


def test_an_unresolvable_target_never_reaches_policy_or_the_driver(rig) -> None:
    """Short-circuit: there is nothing to authorize for an action that cannot happen."""
    dispatcher, driver, bus = rig
    outcome = dispatcher.execute(_click("Nonexistent"), snapshot=_page(), **SESSION)

    assert outcome.status is DispatchStatus.UNRESOLVED
    assert outcome.failure_code is not None and outcome.failure_code.value == "target_not_found"
    assert driver.dispatched == []
    assert [event["event"] for event in bus.read_events()] == [EventType.RESOLVE.value]


def test_an_ambiguous_target_is_refused_not_guessed(rig) -> None:
    dispatcher, driver, bus = rig
    outcome = dispatcher.execute(_click("Submit"), snapshot=_page(), **SESSION)

    assert outcome.status is DispatchStatus.UNRESOLVED
    assert outcome.failure_code is not None and outcome.failure_code.value == "target_ambiguous"
    assert driver.dispatched == []

    resolve_event = bus.read_events()[0]
    assert resolve_event["resolved"] is False
    assert len(resolve_event["candidates"]) == 2, "the refusal must name what it saw"


def test_a_denied_action_is_authorized_against_but_never_dispatched(rig) -> None:
    """Policy sees the resolved control -- "Transfer Funds" -- and refuses. The attempt is
    recorded: a blocked irreversible action is exactly the event an auditor wants to find."""
    dispatcher, driver, bus = rig
    outcome = dispatcher.execute(_click("Transfer Funds"), snapshot=_page(), **SESSION)

    assert outcome.status is DispatchStatus.DENIED
    assert driver.dispatched == []

    events = bus.read_events()
    authorize = next(event for event in events if event["event"] == EventType.AUTHORIZE.value)
    assert authorize["granted"] is False
    assert authorize["risk"] == "irreversible"
    assert not any(event["event"] == EventType.DISPATCH.value for event in events)


def test_an_entrypoint_outside_the_allowlist_is_refused(rig) -> None:
    """Opening a target is setup, not a declared step -- and still may not leave the allowlist.

    `cua discover --target ...` and the operator console both take the URL from the caller, and both
    used to reach `driver.page.goto` directly. That skipped the one check that unambiguously applies
    to a navigation, so a typo -- or a malicious argument -- pointed the browser anywhere.
    """
    from cua.runtime.dispatcher import NavigationBlockedError

    dispatcher, driver, _ = rig
    with pytest.raises(NavigationBlockedError):
        dispatcher.open_entrypoint("http://evil.example.com/", session_id="s")
    assert driver.dispatched == []


def test_off_allowlist_navigation_is_blocked(rig) -> None:
    dispatcher, driver, _ = rig
    outcome = dispatcher.execute(
        Navigate(url="http://evil.example.com"), snapshot=_page(), **SESSION
    )
    assert outcome.status is DispatchStatus.DENIED
    assert outcome.failure_code is not None
    assert outcome.failure_code.value == "navigation_blocked"
    assert driver.dispatched == []


# ------------------------------------------------------------------ lease


def test_a_stale_lease_epoch_is_refused_before_anything_else(rig) -> None:
    """The race this design exists to kill: escalation happens *while* a step is in flight, and
    without this check the in-flight step lands after a human has taken over."""
    dispatcher, driver, _ = rig
    outcome = dispatcher.execute(
        _click("Search"),
        snapshot=_page(),
        actor=Actor.AUTOMATION,
        session_id="sess-1",
        lease_epoch=1,
        expected_epoch=2,
    )

    assert outcome.status is DispatchStatus.LEASE_LOST
    assert driver.dispatched == []


# -------------------------------------------------------------- human path


def test_a_human_needs_confirmation_for_an_irreversible_action(rig) -> None:
    dispatcher, driver, _ = rig
    page = _page()

    pending = dispatcher.execute(
        _click("Transfer Funds"), snapshot=page, actor=Actor.HUMAN, session_id="s", lease_epoch=1
    )
    assert pending.status is DispatchStatus.NEEDS_CONFIRMATION
    assert driver.dispatched == []

    allowed = dispatcher.execute(
        _click("Transfer Funds"),
        snapshot=page,
        actor=Actor.HUMAN,
        session_id="s",
        lease_epoch=1,
        confirmed=True,
    )
    assert allowed.status is DispatchStatus.OK
    assert driver.dispatched[0].actor is Actor.HUMAN


def test_human_actions_are_tagged_in_the_evidence(rig) -> None:
    """ "Record what the human did" is not a feature -- it is a consequence of routing their input
    through the same path."""
    dispatcher, _, bus = rig
    dispatcher.execute(
        _click("Search"), snapshot=_page(), actor=Actor.HUMAN, session_id="s", lease_epoch=7
    )

    dispatch = next(e for e in bus.read_events() if e["event"] == EventType.DISPATCH.value)
    assert dispatch["actor"] == "human"
    assert dispatch["lease_epoch"] == 7


# ------------------------------------------------------------------ drift


def test_drift_is_reported_without_failing_the_step(rig) -> None:
    """Resolving more weakly than recorded is a signal, not an error. Failing on it would make the
    early warning useless, because nobody keeps a warning that breaks things."""
    from cua.domain.target import ResolutionStrategy

    dispatcher, _, bus = rig
    action = Click(
        target=TargetDescriptor(
            role="button",
            name=NameMatch(value="  search  "),
            scope=NodeScope(frame="content"),
            recorded_strategy=ResolutionStrategy.SEMANTIC_EXACT,
        )
    )
    outcome = dispatcher.execute(action, snapshot=_page(), **SESSION)

    assert outcome.status is DispatchStatus.OK
    assert outcome.drifted is True
    resolve = next(e for e in bus.read_events() if e["event"] == EventType.RESOLVE.value)
    assert resolve["drifted"] is True
    assert resolve["strategy"] == "semantic_normalized"


def test_observation_needs_no_authorization_but_is_recorded(rig) -> None:
    """Observing changes nothing, so it is not gated. It is recorded anyway: a failure is far
    easier to explain when you can see what the system perceived at each step."""
    dispatcher, _, bus = rig
    dispatcher.observe()

    observe = next(e for e in bus.read_events() if e["event"] == EventType.OBSERVE.value)
    assert observe["node_count"] == 5
    assert observe["snapshot_ref"].startswith("snapshots/")


def test_a_click_that_navigates_off_the_allowlist_is_refused(tmp_path: Path) -> None:
    """The allowlist has to constrain where the session *lands*, not only where it says it is going.

    Checking a `navigate` action's stated URL covers the case where the system decides to leave. It
    misses the case that matters more: a click on a link, on a page whose content is untrusted, that
    redirects off the permitted host. The action carries no URL, so there was nothing to check up
    front and nothing checked afterwards — `result.navigated` was recorded in the evidence and read
    by nobody.
    """
    config = parse_policy(POLICY.read_text())
    bus = EvidenceBus(tmp_path, Redactor(config.redaction), run_id="run-redirect")
    driver = FakeDriver(_page(), lands_on="https://evil.example.com/phish")
    dispatcher = Dispatcher(
        driver=driver,
        policy=PolicyEngine(config),
        resolver=TargetResolver(),
        evidence=bus,
    )

    outcome = dispatcher.execute(
        Click(target=TargetDescriptor(role="button", name=NameMatch(value="Search"))),
        snapshot=_page(),
        **SESSION,
    )

    assert outcome.status is DispatchStatus.DENIED, outcome.message
    assert outcome.failure_code is FailureCode.NAVIGATION_BLOCKED
    assert "evil.example.com" in outcome.message
    # The click itself was legitimately authorized and did happen — it is the destination that is
    # refused, and saying so is what makes the result debuggable.
    assert driver.dispatched, "the action was permitted; only where it landed was not"
