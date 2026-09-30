"""Shared, browser-free rig for the human-in-the-loop edge-case tests.

Not a test module in its own right (it holds no tests); it is named like one so it lives beside
the files that import it and follows the `test_edge_hitl_*` convention.

Everything here is offline. The surface is a recording fake that, like the one in
`test_dispatcher.py`, refuses to accept anything that is not an `AuthorizedAction` -- a double that
accepted any object would let the chokepoint rot while the suite stayed green. The broker, the
dispatcher, the policy engine and the evidence bus are all the real ones, so what these tests
observe is the behaviour of the production path and not of a stand-in for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cua.domain.snapshot import NodeScope, UiNode, UiSnapshot
from cua.evidence.bus import EvidenceBus
from cua.hitl.broker import SessionBroker
from cua.policy.authorized import AuthorizedAction
from cua.policy.config import parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.redact import Redactor
from cua.runtime.dispatcher import Dispatcher
from cua.surfaces.base import ActionResult, SessionInfo
from cua.targeting.resolver import TargetResolver

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"

# Seeded regulated values: none of these may come out of the operator's view or the evidence.
SSN = "123-45-6789"
ACCOUNT = "0001234501"
EMAIL = "ada@example.com"


def page(url: str = "http://localhost:8811/search", *, value: str | None = None) -> UiSnapshot:
    """A small member-search screen. `value` is what the Member Number box holds."""
    return UiSnapshot(
        snapshot_id="snap",
        url=url,
        title="MemberDesk",
        nodes=(
            UiNode(
                node_id="n1",
                role="textbox",
                name="Member Number",
                value=value,
                scope=NodeScope(frame="content"),
            ),
            UiNode(node_id="n2", role="button", name="Search", scope=NodeScope(frame="content")),
            UiNode(
                node_id="n3", role="button", name="Transfer Funds", scope=NodeScope(frame="content")
            ),
        ),
    )


@dataclass
class RecordingDriver:
    """A surface that remembers exactly what reached it."""

    snapshot: UiSnapshot = field(default_factory=page)
    dispatched: list[AuthorizedAction] = field(default_factory=list)
    screenshots: list[tuple] = field(default_factory=list)
    lands_on: str | None = None
    fail_dispatch: bool = False
    observe_error: Exception | None = None

    def observe(self) -> UiSnapshot:
        if self.observe_error is not None:
            raise self.observe_error
        return self.snapshot

    def dispatch(self, action: AuthorizedAction) -> ActionResult:
        assert isinstance(action, AuthorizedAction), "a driver must only ever see authorized work"
        self.dispatched.append(action)
        if self.fail_dispatch:
            return ActionResult(ok=False, message="the page did not respond")
        return ActionResult(ok=True, navigated=self.lands_on is not None, duration_ms=1)

    def screenshot(self, *, redact: tuple = ()) -> bytes:
        self.screenshots.append(redact)
        return b"\x89PNG-edge"

    def bounds_for(self, node_ids: tuple[str, ...]) -> dict:
        return {}

    def session_info(self) -> SessionInfo:
        return SessionInfo(
            session_id="sess-edge",
            driver="recording",
            url=self.lands_on or self.snapshot.url,
            capabilities=("semantic_tree",),
        )

    def close(self) -> None:
        return None


@dataclass
class Rig:
    broker: SessionBroker
    driver: RecordingDriver
    dispatcher: Dispatcher
    bus: EvidenceBus


def build_rig(tmp_path: Path, *, run_id: str = "edge-hitl") -> Rig:
    """The real broker, dispatcher, policy and evidence bus over a recording fake surface."""
    config = parse_policy(POLICY.read_text())
    bus = EvidenceBus(tmp_path, Redactor(config.redaction), run_id=run_id)
    driver = RecordingDriver()
    dispatcher = Dispatcher(
        driver=driver,
        policy=PolicyEngine(config),
        resolver=TargetResolver(allow_vision=False),
        evidence=bus,
    )
    broker = SessionBroker(session_id="sess-edge", evidence=bus, dispatch=dispatcher)
    return Rig(broker=broker, driver=driver, dispatcher=dispatcher, bus=bus)


def escalate(rig: Rig, **overrides: object):
    """Open an intervention with sensible defaults. Keyword overrides replace any field."""
    args: dict = {
        "run_id": "run-1",
        "capability_ref": "corebank.member.savings_balance@1.0.0",
        "goal": "Read a member's savings balance",
        "reason": "the resolver refused an ambiguous control",
        "step_id": "submit_search",
        "failure_code": "target_ambiguous",
        "snapshot": rig.driver.snapshot,
    }
    args.update(overrides)
    return rig.broker.escalate(**args)


def events(rig: Rig, kind: str) -> list[dict]:
    return [e for e in rig.bus.read_events() if e["event"] == kind]
