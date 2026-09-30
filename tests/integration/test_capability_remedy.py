"""A `run_capability` recovery remedy runs another capability through the ordinary chokepoint.

The schema has always accepted `run_capability` -- the reference artifact's `session_expired` rule
uses it -- and the executor answered every one with RECOVERY_EXHAUSTED, "not implemented". These
assert what it does now, and the bounds that keep it from becoming a way around them: the remedy's
actions are resolved, authorized and recorded like any other; it runs one level deep and never names
itself; and its failure is the run's failure, escalated once.

Driven against a fake surface -- a sign-on screen that becomes a search screen that becomes a result
-- because the claims here are about the executor's control flow and its evidence, not a browser.
`tests/integration/test_fault_matrix.py` replays the reference artifact against the real app's
session timeout.
"""

from __future__ import annotations

from pathlib import Path

from cua.domain.capability import Capability
from cua.domain.result import FailureCode, RunStatus
from cua.domain.serde import load_capability
from cua.domain.snapshot import UiNode, UiSnapshot
from cua.evidence.bus import EventType, EvidenceBus
from cua.policy.authorized import AuthorizedAction
from cua.policy.config import parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.redact import Redactor
from cua.replay.executor import ReplayExecutor
from cua.runtime.dispatcher import Dispatcher
from cua.surfaces.base import ActionResult, SessionInfo
from cua.targeting.resolver import TargetResolver

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"
URL = "http://localhost:8811/"

LOOKUP = """
schema_version: "1.0.0"
id: test.member.lookup
version: 1.0.0
title: Run a search
surface: {kind: legacy_web, app: {vendor: acme-core, product: MemberDesk}}
entrypoint: {url_pattern: "http://localhost:8811/"}
steps:
  - id: search
    action: {type: click, target: {role: button, name: {value: Search, match: exact}}}
    precondition: {assert: node_exists, query: {role: button, name: Search}}
checkpoint: {assert: node_exists, query: {role: cell, name: Done}}
recovery:
  - id: session_expired
    detect: {assert: node_exists, query: {role: button, name: Sign On}}
    remedy: [{type: run_capability, capability_id: test.auth.sign_on}]
    max_attempts: 1
"""

SIGN_ON = """
schema_version: "1.0.0"
id: test.auth.sign_on
version: 1.0.0
title: Sign back on
surface: {kind: legacy_web, app: {vendor: acme-core, product: MemberDesk}}
entrypoint: {url_pattern: "http://localhost:8811/"}
steps:
  - id: sign_on
    action: {type: click, target: {role: button, name: {value: Sign On, match: exact}}}
    precondition: {assert: node_exists, query: {role: button, name: Sign On}}
checkpoint: {assert: node_exists, query: {role: button, name: Search}}
outcomes:
  - code: invalid_credentials
    detect: {assert: text_present, value: Invalid operator ID}
"""


class Desk:
    """Signed out -> (Sign On) -> search screen -> (Search) -> result. Or a refused sign-on."""

    def __init__(self, *, accepts_sign_on: bool = True) -> None:
        self.screen = "signed_out"
        self.accepts_sign_on = accepts_sign_on
        self.dispatched: list[AuthorizedAction] = []

    def observe(self) -> UiSnapshot:
        nodes = {
            "signed_out": (UiNode(node_id="n1", role="button", name="Sign On"),),
            "refused": (
                UiNode(node_id="n1", role="button", name="Sign On"),
                UiNode(node_id="n9", role="cell", name="Invalid operator ID or password."),
            ),
            "search": (UiNode(node_id="n2", role="button", name="Search"),),
            "done": (UiNode(node_id="n3", role="cell", name="Done"),),
        }[self.screen]
        return UiSnapshot(snapshot_id=self.screen, url=URL, nodes=nodes)

    def dispatch(self, action: AuthorizedAction) -> ActionResult:
        assert isinstance(action, AuthorizedAction), "the driver must only ever see authorized work"
        self.dispatched.append(action)
        if action.resolved_node_id == "n1":
            self.screen = "search" if self.accepts_sign_on else "refused"
        elif action.resolved_node_id == "n2":
            self.screen = "done"
        return ActionResult(ok=True, duration_ms=1)

    def screenshot(self, *, redact: tuple = ()) -> bytes:
        return b""

    def bounds_for(self, node_ids: tuple[str, ...]) -> dict:
        return {}

    def session_info(self) -> SessionInfo:
        return SessionInfo(session_id="s", driver="fake", url=URL)

    def close(self) -> None:
        return None


def _sealed(text: str) -> Capability:
    return load_capability(text).with_hash()


def _rig(tmp_path: Path, desk: Desk) -> tuple[Dispatcher, EvidenceBus]:
    config = parse_policy(POLICY.read_text())
    bus = EvidenceBus(tmp_path, Redactor(config.redaction), run_id="remedy")
    dispatcher = Dispatcher(
        driver=desk, policy=PolicyEngine(config), resolver=TargetResolver(), evidence=bus
    )
    return dispatcher, bus


def _events(bus: EvidenceBus, name: EventType) -> list[dict]:
    return [e for e in bus.read_events() if e["event"] == name.value]


def _clicked(desk: Desk) -> list[str | None]:
    return [a.resolved_node_id for a in desk.dispatched if a.action.type == "click"]


def test_a_capability_remedy_runs_through_the_chokepoint_and_the_run_carries_on(
    tmp_path: Path,
) -> None:
    desk = Desk()
    dispatcher, bus = _rig(tmp_path, desk)
    sign_on = _sealed(SIGN_ON)

    result = ReplayExecutor(
        dispatcher=dispatcher, evidence=bus, capabilities={sign_on.id: sign_on}.__getitem__
    ).run(_sealed(LOOKUP), {})

    assert result.status is RunStatus.SUCCESS, result.summary
    assert result.recovery_attempts == 1
    assert _clicked(desk) == ["n1", "n2"], "signed on, then the interrupted step ran"
    # It acts on the screen it was called to repair: the only navigation is the run's own entry.
    assert [a.action.type for a in desk.dispatched].count("navigate") == 1

    # Every action the remedy took was authorized and recorded, like any other.
    assert bus.unauthorized_dispatches() == []
    remedy = [e for e in _events(bus, EventType.RECOVERY) if e.get("capability")]
    assert remedy and remedy[0]["capability"] == "test.auth.sign_on@1.0.0"
    assert remedy[0]["content_hash"] == sign_on.content_hash, "which content ran must be named"

    # The remedy's ending is noted against the run; the run ends exactly once.
    ended = _events(bus, EventType.RUN_END)
    assert len(ended) == 1 and ended[0]["status"] == "success"
    assert any(
        e.get("note") == "recovery capability ended" and e.get("status") == "success"
        for e in _events(bus, EventType.NOTE)
    )


def test_without_a_capability_source_the_remedy_fails_closed(tmp_path: Path) -> None:
    desk = Desk()
    dispatcher, bus = _rig(tmp_path, desk)

    result = ReplayExecutor(dispatcher=dispatcher, evidence=bus).run(_sealed(LOOKUP), {})

    assert result.status is RunStatus.NEEDS_HUMAN, "recovery_exhausted is an escalation trigger"
    assert result.error is not None and result.error.code is FailureCode.RECOVERY_EXHAUSTED
    assert "no capability source" in result.error.message
    assert _clicked(desk) == []


def test_an_unknown_remedy_capability_fails_closed(tmp_path: Path) -> None:
    desk = Desk()
    dispatcher, bus = _rig(tmp_path, desk)

    result = ReplayExecutor(dispatcher=dispatcher, evidence=bus, capabilities={}.__getitem__).run(
        _sealed(LOOKUP), {}
    )

    assert result.error is not None and result.error.code is FailureCode.RECOVERY_EXHAUSTED
    assert _clicked(desk) == []


def test_an_unsealed_remedy_is_refused(tmp_path: Path) -> None:
    """Looked up by name at run time, so the evidence must be able to say exactly what ran."""
    desk = Desk()
    dispatcher, bus = _rig(tmp_path, desk)
    unsealed = load_capability(SIGN_ON)

    result = ReplayExecutor(
        dispatcher=dispatcher, evidence=bus, capabilities={unsealed.id: unsealed}.__getitem__
    ).run(_sealed(LOOKUP), {})

    assert result.error is not None and result.error.code is FailureCode.RECOVERY_EXHAUSTED
    assert "content hash" in result.error.message
    assert _clicked(desk) == []


def test_a_capability_cannot_be_its_own_remedy(tmp_path: Path) -> None:
    desk = Desk()
    dispatcher, bus = _rig(tmp_path, desk)
    selfish = _sealed(
        LOOKUP.replace("capability_id: test.auth.sign_on", "capability_id: test.member.lookup")
    )

    result = ReplayExecutor(
        dispatcher=dispatcher, evidence=bus, capabilities={selfish.id: selfish}.__getitem__
    ).run(selfish, {})

    assert result.error is not None and result.error.code is FailureCode.RECOVERY_EXHAUSTED
    assert "its own remedy" in result.error.message
    assert _clicked(desk) == []


def test_remedies_do_not_nest(tmp_path: Path) -> None:
    """A remedy that itself reaches for a capability remedy is refused, not followed -- which is
    what makes a chain of them, or a cycle between two, terminate."""
    desk = Desk()
    dispatcher, bus = _rig(tmp_path, desk)
    chaining = _sealed(
        SIGN_ON
        + """
recovery:
  - id: still_signed_out
    detect: {assert: node_exists, query: {role: button, name: Sign On}}
    remedy: [{type: run_capability, capability_id: test.member.lookup}]
    max_attempts: 1
"""
    )
    lookup = _sealed(LOOKUP)
    source = {chaining.id: chaining, lookup.id: lookup}

    result = ReplayExecutor(
        dispatcher=dispatcher, evidence=bus, capabilities=source.__getitem__
    ).run(lookup, {})

    assert result.error is not None and result.error.code is FailureCode.RECOVERY_EXHAUSTED
    assert "remedies do not nest" in result.error.message
    assert _clicked(desk) == []
    assert len(_events(bus, EventType.ESCALATE)) == 1, "one failure, one escalation"


def test_a_failing_remedy_is_the_runs_failure_escalated_once(tmp_path: Path) -> None:
    """The remedy's refusal is not a second terminal event and not a second intervention -- it is
    reported to the run, which escalates by its own triggers."""
    desk = Desk(accepts_sign_on=False)
    dispatcher, bus = _rig(tmp_path, desk)
    sign_on = _sealed(SIGN_ON)

    result = ReplayExecutor(
        dispatcher=dispatcher, evidence=bus, capabilities={sign_on.id: sign_on}.__getitem__
    ).run(_sealed(LOOKUP), {})

    assert result.status is RunStatus.NEEDS_HUMAN
    assert result.error is not None and result.error.code is FailureCode.RECOVERY_EXHAUSTED
    assert "invalid_credentials" in result.error.message, "the remedy's own answer is carried up"
    assert _clicked(desk) == ["n1"], "tried once, and the interrupted step never ran"
    assert len(_events(bus, EventType.ESCALATE)) == 1
    assert _events(bus, EventType.RUN_END) == []


def test_the_rule_budget_still_bounds_a_remedy_that_does_not_help(tmp_path: Path) -> None:
    """A remedy that reports success while the condition persists is retried only as often as the
    rule allows, then RECOVERY_EXHAUSTED -- the same bound a `reload` remedy has."""
    desk = Desk()
    dispatcher, bus = _rig(tmp_path, desk)
    # "Succeeds" by its own checkpoint without changing the screen.
    hollow = _sealed(
        SIGN_ON.replace(
            "checkpoint: {assert: node_exists, query: {role: button, name: Search}}",
            "checkpoint: {assert: node_exists, query: {role: button, name: Sign On}}",
        ).replace(
            "action: {type: click, target: {role: button, name: {value: Sign On, match: exact}}}",
            "action: {type: assert, that: {assert: node_exists, query: {role: button, "
            "name: Sign On}}}",
        )
    )

    result = ReplayExecutor(
        dispatcher=dispatcher, evidence=bus, capabilities={hollow.id: hollow}.__getitem__
    ).run(_sealed(LOOKUP), {})

    assert result.error is not None and result.error.code is FailureCode.RECOVERY_EXHAUSTED
    assert "after 1 attempt(s)" in result.error.message
    assert result.recovery_attempts == 2
    assert _clicked(desk) == []
