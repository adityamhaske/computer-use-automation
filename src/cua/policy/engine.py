"""The authorization chokepoint.

`PolicyEngine.authorize()` is the only way to obtain an `AuthorizedAction`, and
`SurfaceDriver.dispatch()` accepts nothing else. Every actor -- the discovery agent, the replay
executor, and a human operator during an intervention -- passes through here.

### A correction to the documented pipeline

The design originally specified:

    Action -> PolicyEngine -> TargetResolver -> SurfaceDriver

Implementing it exposed an ordering problem. Risk classification's second signal is *what the
control says it does* ("Transfer Funds"), and that requires the resolved node -- so authorizing
before resolving means authorizing half-blind, on action type alone. The actual pipeline is:

    Action -> TargetResolver -> PolicyEngine -> SurfaceDriver
              (pure, read-only)  (authorize)     (dispatch)

This does not weaken the chokepoint, because the invariant was never "policy runs first" -- it is
**nothing reaches a surface without authorization**. Resolution is a pure function over a snapshot
that was already captured; it touches nothing and changes nothing. Running it first means policy
decides with full knowledge of what is about to be acted on, which is strictly safer.

A resolution failure short-circuits: there is nothing to authorize, so policy is never consulted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from cua.domain.action import HUMAN_ONLY_ACTIONS, Action, ActionRisk
from cua.domain.actor import Actor
from cua.domain.capability import Capability
from cua.domain.snapshot import UiNode
from cua.policy.allowlist import AllowlistCheck
from cua.policy.authorized import MINT_TOKEN, AuthorizedAction
from cua.policy.config import PolicyConfig
from cua.policy.risk import RiskAssessment, RiskClassifier


class Outcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"
    """Permitted, but only with a human explicitly confirming. This is what an operator's authority
    during an escalation actually looks like: wider than automation's, and recorded."""


@dataclass(frozen=True)
class PolicyDecision:
    outcome: Outcome
    decision_id: str
    risk: RiskAssessment
    reason: str
    actor: Actor
    authorized: AuthorizedAction | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome is Outcome.ALLOW


class PolicyDeniedError(RuntimeError):
    """Policy refused. Carries the decision so the refusal is explainable."""

    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


@dataclass
class PolicyEngine:
    """Evaluates every action, for every actor, against the configured policy."""

    config: PolicyConfig

    def __post_init__(self) -> None:
        self._allowlist = AllowlistCheck(self.config.allowlist)
        self._risk = RiskClassifier(self.config.risk)

    def authorize(
        self,
        action: Action,
        *,
        actor: Actor,
        session_id: str,
        lease_epoch: int,
        target: UiNode | None = None,
        capability: Capability | None = None,
        declared_risk: ActionRisk | None = None,
        confirmed: bool = False,
    ) -> PolicyDecision:
        """Decide, and on approval mint the only token a driver will accept."""
        decision_id = f"dec-{uuid.uuid4().hex[:12]}"

        def deny(reason: str, risk: RiskAssessment) -> PolicyDecision:
            return PolicyDecision(Outcome.DENY, decision_id, risk, reason, actor)

        baseline = RiskAssessment(ActionRisk.SAFE, ())

        # --- 1. Is this action type permitted at all? ------------------------
        if action.type in HUMAN_ONLY_ACTIONS and actor is not Actor.HUMAN:
            return deny(f"{action.type!r} may only be originated by a human operator", baseline)
        globally_denied = (
            self.config.actions.allowed
            and action.type not in self.config.actions.allowed
            and (action.type not in self.config.actions.human_only or actor is not Actor.HUMAN)
        )
        if globally_denied:
            return deny(f"action type {action.type!r} is not permitted", baseline)

        # --- 2. A capability may narrow the action set, never widen it -------
        if (
            capability
            and capability.policy.allowed_actions
            and action.type not in capability.policy.allowed_actions
        ):
            return deny(f"{action.type!r} is outside this capability's declared actions", baseline)

        # --- 3. Navigation must stay inside the allowlist --------------------
        # The primary defense against a prompt-injection redirect: page text is untrusted, and
        # policy is evaluated outside the model that read it.
        if action.type == "navigate":
            url = getattr(action, "url", "")
            extra = capability.policy.allowed_domains if capability else ()
            verdict = AllowlistCheck(self.config.allowlist, extra_domains=tuple(extra)).check(url)
            if not verdict.allowed:
                return deny(f"navigation blocked: {verdict.reason}", baseline)

        # --- 4. How dangerous is this? ---------------------------------------
        risk = self._risk.classify(action, target=target, declared=declared_risk)
        disposition = self.config.disposition_for(actor, risk.tier)

        if disposition == "block_and_escalate":
            return deny(f"{risk.tier.value} action requires a human: {risk.explain()}", risk)

        if disposition == "allow_if_declared" and declared_risk is None and action.risk is None:
            # An elevated action must be one the artifact anticipated. An agent improvising a form
            # submission that no reviewer saw is exactly what this blocks.
            return deny(
                f"{risk.tier.value} action is not declared by the capability: {risk.explain()}",
                risk,
            )

        if disposition == "require_confirmation" and not confirmed:
            return PolicyDecision(
                Outcome.REQUIRE_CONFIRMATION,
                decision_id,
                risk,
                f"{risk.tier.value} action needs explicit confirmation: {risk.explain()}",
                actor,
            )

        return PolicyDecision(
            outcome=Outcome.ALLOW,
            decision_id=decision_id,
            risk=risk,
            reason=risk.explain(),
            actor=actor,
            authorized=AuthorizedAction(
                action=action,
                actor=actor,
                session_id=session_id,
                lease_epoch=lease_epoch,
                decision_id=decision_id,
                risk=risk.tier,
                resolved_node_id=target.node_id if target else None,
                requires_confirmation=disposition == "require_confirmation",
                _token=MINT_TOKEN,
            ),
        )

    def authorize_or_raise(self, action: Action, **kwargs: object) -> AuthorizedAction:
        decision = self.authorize(action, **kwargs)  # type: ignore[arg-type]
        if decision.authorized is None:
            raise PolicyDeniedError(decision)
        return decision.authorized
