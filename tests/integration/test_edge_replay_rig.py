"""Shared scaffolding for the replay edge-case suites: a scriptable fake surface under the real
chokepoint.

Every `test_edge_replay_executor_*` module builds on this, so the edge cases drive the *real*
`ReplayExecutor`, `Dispatcher`, `PolicyEngine`, `TargetResolver` and `EvidenceBus` under the shipped
`config/policy.yaml` -- and fake only the thing that needs a browser. That is deliberate: the claims
in those suites are about the executor's control flow, its budgets and its evidence, none of which a
Chromium process adds information to, and all of which a browser makes slow and order-dependent.

What is faked, stated rather than hidden: `Desk` is a `SurfaceDriver` whose screens and click
transitions are declared as data. It never touches a network, binds no port, and only accepts an
`AuthorizedAction` (the same guard the real driver has), so a test that could reach it without
passing policy would fail here rather than pass quietly.

This module also carries the two tests that keep the scaffolding honest -- the rig must enforce the
chokepoint and must be able to fail -- because a fake that cannot fail makes every suite built on it
vacuous.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from cua.domain.capability import Capability
from cua.domain.result import RunStatus
from cua.domain.snapshot import UiNode, UiSnapshot
from cua.evidence.bus import EventType, EvidenceBus
from cua.hitl.broker import SessionBroker
from cua.policy.authorized import AuthorizedAction
from cua.policy.config import PolicyConfig, parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.redact import Redactor
from cua.policy.secrets import SecretResolver
from cua.replay import wait as wait_module
from cua.replay.executor import ReplayExecutor
from cua.runtime.dispatcher import Dispatcher
from cua.surfaces.base import ActionResult, SessionInfo
from cua.targeting.resolver import TargetResolver

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"

# Allowlisted by the shipped policy. It is only ever a *string* here: nothing in these suites opens
# a
# socket, so the ports other processes use are never touched.
URL = "http://localhost:8811/"
EVIL_URL = "http://evil.example.com/collect"

LOOKUP_YAML = """
schema_version: "1.0.0"
id: edge.member.lookup
version: 1.0.0
title: Look up a member
surface: {kind: legacy_web, app: {vendor: acme-core, product: MemberDesk}}
entrypoint: {url_pattern: "http://localhost:8811/"}
inputs:
  - {name: member_id, pattern: "^[0-9]{4,10}$"}
outputs:
  - {name: balance, type: money}
  - {name: status}
steps:
  - id: enter_member
    action:
      type: type
      value: {$input: member_id}
      target: {role: textbox, name: {value: Member Number, match: exact}}
    precondition: {assert: node_exists, query: {role: textbox, name: Member Number}}
    postcondition:
      assert: node_has_value
      query: {role: textbox, name: Member Number}
      value: {$input: member_id}
  - id: submit_search
    action: {type: click, target: {role: button, name: {value: Search, match: exact}}}
    risk: elevated
    postcondition: {assert: node_absent, query: {role: textbox, name: Member Number}}
  - id: read_balance
    precondition: {assert: node_exists, query: {role: cell, name: Balance}}
    action:
      type: extract
      into: balance
      transform: money
      target: {role: cell, name: {value: Balance, match: exact}}
  - id: read_status
    precondition: {assert: node_exists, query: {role: cell, name: Status}}
    action:
      type: extract
      into: status
      target: {role: cell, name: {value: Status, match: exact}}
checkpoint:
  assert: all_of
  of:
    - {assert: node_exists, query: {role: cell, name: Balance}}
    - {assert: node_exists, query: {role: cell, name: Status}}
outcomes:
  - code: member_not_found
    detect: {assert: text_present, value: No records found}
    returns: {member_id: {$input: member_id}}
recovery:
  - id: transient_load
    detect: {assert: http_status_in, codes: [502, 503, 504]}
    remedy: [{type: reload}]
    max_attempts: 3
"""


def lookup(mutate: Callable[[dict[str, Any]], None] | None = None) -> Capability:
    """The reference lookup flow, sealed. `mutate` edits the plain-dict form before validation.

    Editing the dict rather than copying a built model keeps each test's deviation from the baseline
    visible in one place, and lets a test exercise the *schema's* own refusals (an undeclared
    output, a duplicate step id) instead of building something the loader would never accept.
    """
    document = copy.deepcopy(yaml.safe_load(LOOKUP_YAML))
    if mutate is not None:
        mutate(document)
    return Capability.model_validate(document).with_hash()


def step_of(document: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next(s for s in document["steps"] if s["id"] == step_id)


# ---------------------------------------------------------------------------- the fake surface


@dataclass
class Screen:
    """One page the desk can show. `clicks` maps a node id to the screen a click on it leads to."""

    nodes: tuple[UiNode, ...]
    url: str = URL
    http_status: int | None = None
    clicks: dict[str, str | Callable[[Desk], str]] = field(default_factory=dict)


class Desk:
    """A scriptable `SurfaceDriver`: named screens, click transitions, typed-value reflection."""

    def __init__(self, screens: dict[str, Screen], *, start: str, entry: str | None = None) -> None:
        self.screens = screens
        self.screen = start
        self.entry = entry or start
        self.fields: dict[str, str] = {}
        self.dispatched: list[AuthorizedAction] = []
        self.observations = 0
        self.outages: dict[str, int] = {}
        """Screen name -> reloads still needed before it stops answering 503."""
        self.on_dispatch: Callable[[AuthorizedAction], None] | None = None
        self.on_click: Callable[[Desk, str | None], ActionResult | None] | None = None

    @property
    def current(self) -> Screen:
        return self.screens[self.screen]

    def observe(self) -> UiSnapshot:
        self.observations += 1
        screen = self.current
        nodes = tuple(
            node.model_copy(update={"value": self.fields[node.node_id]})
            if node.node_id in self.fields
            else node
            for node in screen.nodes
        )
        down = self.outages.get(self.screen, 0) > 0
        return UiSnapshot(
            snapshot_id=self.screen,
            url=screen.url,
            nodes=nodes,
            http_status=503 if down else screen.http_status,
        )

    def dispatch(self, action: AuthorizedAction) -> ActionResult:
        # The same guard the real driver has: a forged action must not get this far.
        assert isinstance(action, AuthorizedAction), "the driver must only ever see authorized work"
        self.dispatched.append(action)
        if self.on_dispatch is not None:
            self.on_dispatch(action)

        kind = action.action.type
        if kind == "type":
            self.fields[action.resolved_node_id or "?"] = str(action.action.value)  # type: ignore[union-attr]
        elif kind == "click":
            if self.on_click is not None:
                override = self.on_click(self, action.resolved_node_id)
                if override is not None:
                    return override
            target = self.current.clicks.get(action.resolved_node_id or "?")
            if target is not None:
                self.screen = target(self) if callable(target) else target
                self.fields.clear()
                return ActionResult(ok=True, navigated=True, duration_ms=1)
        elif kind == "reload":
            if self.outages.get(self.screen, 0) > 0:
                self.outages[self.screen] -= 1
        elif kind == "navigate":
            self.screen = self.entry
            self.fields.clear()
            return ActionResult(ok=True, navigated=True, duration_ms=1)
        return ActionResult(ok=True, duration_ms=1)

    def screenshot(self, *, redact: tuple = ()) -> bytes:
        return b""

    def bounds_for(self, node_ids: tuple[str, ...]) -> dict:
        return {}

    def session_info(self) -> SessionInfo:
        return SessionInfo(session_id="edge", driver="fake-desk", url=self.current.url)

    def close(self) -> None:
        return None

    # ---- what a test asks about afterwards

    def actions(self, kind: str) -> list[AuthorizedAction]:
        return [a for a in self.dispatched if a.action.type == kind]


def member_desk(
    *,
    balance: str | None = "$4,210.55",
    status: str | None = "Active",
    known: tuple[str, ...] = ("12345", "67890"),
    extra_record_nodes: tuple[UiNode, ...] = (),
) -> Desk:
    """Search form -> (Search) -> member record, or a "No records found" screen for an unknown
    id."""

    def after_search(desk: Desk) -> str:
        return "record" if desk.fields.get("n_member") in known else "missing"

    return Desk(
        {
            "search": Screen(
                nodes=(
                    UiNode(node_id="n_member", role="textbox", name="Member Number"),
                    UiNode(node_id="n_search", role="button", name="Search"),
                ),
                clicks={"n_search": after_search},
            ),
            "record": Screen(
                nodes=(
                    UiNode(node_id="n_balance", role="cell", name="Balance", value=balance),
                    UiNode(node_id="n_status", role="cell", name="Status", value=status),
                    *extra_record_nodes,
                )
            ),
            "missing": Screen(
                nodes=(UiNode(node_id="n_none", role="cell", name="No records found"),)
            ),
        },
        start="search",
    )


# ------------------------------------------------------------------------------ wiring a run


@dataclass
class Rig:
    desk: Desk
    dispatcher: Dispatcher
    bus: EvidenceBus
    config: PolicyConfig
    executor: ReplayExecutor
    broker: SessionBroker | None = None

    @property
    def run_dir(self) -> Path:
        return self.bus.run_dir

    def events(self, kind: EventType) -> list[dict[str, Any]]:
        return [e for e in self.bus.read_events() if e["event"] == kind.value]


def make_rig(
    root: Path,
    desk: Desk,
    *,
    supervised: bool = False,
    secrets: dict[str, str] | None = None,
    policy_update: Callable[[PolicyConfig], PolicyConfig] | None = None,
    **executor_kwargs: Any,
) -> Rig:
    """Wire a real dispatcher and executor over `desk`, writing evidence under `root`.

    One bus per rig: `EvidenceBus` documents itself as one run's directory, and a shared one would
    concatenate runs into a single trace -- making any determinism comparison compare a run against
    its own predecessors.
    """
    config = parse_policy(POLICY.read_text())
    if policy_update is not None:
        config = policy_update(config)
    redactor = Redactor(config.redaction)
    bus = EvidenceBus(root / "evidence", redactor, run_id="edge-run")
    resolver = SecretResolver(overrides=secrets or {}, redactor=redactor) if secrets else None
    dispatcher = Dispatcher(
        driver=desk,
        policy=PolicyEngine(config),
        resolver=TargetResolver(allow_vision=False),
        evidence=bus,
        secrets=resolver,
    )
    broker = (
        SessionBroker(session_id="edge-session", evidence=bus, dispatch=dispatcher)
        if supervised
        else None
    )
    executor = ReplayExecutor(
        dispatcher=dispatcher,
        evidence=bus,
        broker=broker,
        session_id="edge-session",
        **executor_kwargs,
    )
    return Rig(
        desk=desk, dispatcher=dispatcher, bus=bus, config=config, executor=executor, broker=broker
    )


def written_text(root: Path) -> str:
    """Every byte of text a run put on disk -- the only honest way to ask "did X leak?"."""
    return "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in {".json", ".jsonl", ".txt", ".yaml", ".log"}
    )


class VirtualClock:
    """A clock a test advances by hand, standing in for `time` inside one module.

    Lets a budget test land *exactly* on a boundary -- "at the limit" versus "one millisecond over"
    -- which a wall clock cannot do without a timing assertion, and a timing assertion is the flake
    this suite exists not to write.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def advance_ms(self, milliseconds: float) -> None:
        self.now += milliseconds / 1000


def virtual_polls(monkeypatch: pytest.MonkeyPatch) -> VirtualClock:
    """Make `cua.replay.wait` poll against a virtual clock: no real sleeping, same decisions.

    Every dispatched step waits for a stable snapshot, which really sleeps one poll interval. The
    interval is not what is under test, so it is virtualised -- the loop still runs, still exits the
    moment its condition holds, and still times out by the declared budget, just without spending
    wall time to do it.
    """
    clock = VirtualClock()
    monkeypatch.setattr(wait_module, "time", clock)
    return clock


# ---------------------------------------------------------------- tests that keep the rig honest


def test_the_fake_surface_completes_the_reference_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The baseline every other suite deviates from. If this fails, nothing built on it means
    much."""
    virtual_polls(monkeypatch)
    rig = make_rig(tmp_path, member_desk())

    result = rig.executor.run(lookup(), {"member_id": "12345"})

    assert result.status is RunStatus.SUCCESS, result.summary
    assert result.outputs == {"balance": "4210.55", "status": "Active"}
    assert rig.bus.unauthorized_dispatches() == []


def test_the_fake_surface_refuses_an_action_that_skipped_policy() -> None:
    """The rig must be able to fail: a driver that accepted a bare `Action` would let every suite
    above it pass while proving nothing about the chokepoint."""
    from cua.domain.action import Click
    from cua.domain.target import TargetDescriptor

    desk = member_desk()
    with pytest.raises(AssertionError, match="authorized"):
        desk.dispatch(Click(target=TargetDescriptor(role="button")))  # type: ignore[arg-type]
    assert desk.dispatched == []
