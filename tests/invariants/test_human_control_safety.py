"""AGENTS.md invariant 4: human input cannot bypass policy or evidence.

The natural implementation of an operator console -- stream the page to a human, forward their
clicks straight into it via CDP -- means that the instant a person takes over, the allowlist, the
risk classification and the audit trail all stop applying. In a system handling regulated financial
data, escalation would be a hole in the security model rather than a feature of it.

So the console submits `raw_input` *actions* through the same chokepoint, under a distinct `HUMAN`
policy profile. Escalation widens authority deliberately and auditably; it does not disable it.

These tests assert both halves: what a human may do that automation may not, and what remains
enforced for both.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cua.domain.action import Click, Navigate, RawInput, RawInputKind
from cua.domain.actor import Actor
from cua.domain.snapshot import UiNode
from cua.domain.target import TargetDescriptor
from cua.policy.config import parse_policy
from cua.policy.engine import Outcome, PolicyEngine

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"
SESSION = {"session_id": "sess-1", "lease_epoch": 4}


@pytest.fixture
def engine() -> PolicyEngine:
    return PolicyEngine(parse_policy(POLICY.read_text()))


def _button(name: str) -> UiNode:
    return UiNode(node_id="n1", role="button", name=name)


# ------------------------------------------- what escalation actually widens


def test_automation_is_blocked_from_an_irreversible_action(engine: PolicyEngine) -> None:
    decision = engine.authorize(
        Click(target=TargetDescriptor(role="button")),
        actor=Actor.AUTOMATION,
        target=_button("Transfer Funds"),
        **SESSION,
    )
    assert decision.outcome is Outcome.DENY
    assert decision.authorized is None


def test_a_human_may_perform_it_but_only_after_confirming(engine: PolicyEngine) -> None:
    """The entire point of escalating: a person can decide what automation may not.

    Note the two-step. An unconfirmed request does not silently succeed because a human is present
    -- it returns REQUIRE_CONFIRMATION, and the console has to ask.
    """
    action = Click(target=TargetDescriptor(role="button"))
    unconfirmed = engine.authorize(
        action, actor=Actor.HUMAN, target=_button("Transfer Funds"), **SESSION
    )
    assert unconfirmed.outcome is Outcome.REQUIRE_CONFIRMATION
    assert unconfirmed.authorized is None

    confirmed = engine.authorize(
        action, actor=Actor.HUMAN, target=_button("Transfer Funds"), confirmed=True, **SESSION
    )
    assert confirmed.outcome is Outcome.ALLOW
    assert confirmed.authorized is not None
    assert confirmed.authorized.actor is Actor.HUMAN


# ------------------------------------------- what stays enforced regardless


def test_a_human_still_cannot_leave_the_allowlist(engine: PolicyEngine) -> None:
    """Taking control widens *what* an operator may do, never *where*.

    An operator who could navigate anywhere would also be a path for a page that tricks them into
    it, which is the same attack the allowlist exists to stop.
    """
    decision = engine.authorize(
        Navigate(url="http://evil.example.com/collect"), actor=Actor.HUMAN, **SESSION
    )
    assert decision.outcome is Outcome.DENY
    assert "navigation blocked" in decision.reason


def test_human_actions_are_attributed(engine: PolicyEngine) -> None:
    """Every authorization carries actor, session and lease epoch, so the evidence stream can say
    who did what and under which grant of control."""
    decision = engine.authorize(
        RawInput(kind=RawInputKind.MOUSE_CLICK, x=120, y=48), actor=Actor.HUMAN, **SESSION
    )
    assert decision.authorized is not None
    assert decision.authorized.actor is Actor.HUMAN
    assert decision.authorized.session_id == "sess-1"
    assert decision.authorized.lease_epoch == 4


def test_automation_cannot_originate_raw_input(engine: PolicyEngine) -> None:
    """`raw_input` exists so a human's console input is policed. If automation could emit it, the
    action would become a way to act without a resolved, named target -- exactly the bypass the
    type was introduced to close."""
    decision = engine.authorize(
        RawInput(kind=RawInputKind.MOUSE_CLICK, x=10, y=10), actor=Actor.AUTOMATION, **SESSION
    )
    assert decision.outcome is Outcome.DENY
    assert "human operator" in decision.reason


def test_human_input_is_still_risk_classified(engine: PolicyEngine) -> None:
    """A human's actions are recorded with a risk tier like any other. "A person did it" is not a
    reason to stop classifying what was done."""
    decision = engine.authorize(
        RawInput(kind=RawInputKind.MOUSE_CLICK, x=10, y=10), actor=Actor.HUMAN, **SESSION
    )
    assert decision.outcome is Outcome.ALLOW
    assert decision.risk.tier.value == "elevated"
    assert decision.risk.signals, "the classification must say why"


def test_an_unknown_actor_gets_the_most_restrictive_treatment(engine: PolicyEngine) -> None:
    """A policy lookup that misses must never be the reason something dangerous is permitted."""
    decision = engine.authorize(
        Click(target=TargetDescriptor(role="button")),
        actor=Actor.SYSTEM,
        target=_button("Delete Account"),
        **SESSION,
    )
    assert decision.outcome is Outcome.DENY
