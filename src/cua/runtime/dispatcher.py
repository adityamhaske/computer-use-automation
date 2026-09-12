"""The only path to a surface.

`cua.runtime.dispatcher` is the sole module permitted to import `cua.surfaces` (enforced by the
`policy-chokepoint` import contract). Everything that wants to act -- the discovery agent, the
replay executor, a human operator during an intervention -- comes through here, and the sequence is
not optional:

    Action -> TargetResolver -> PolicyEngine -> SurfaceDriver -> EvidenceBus
              (pure, read-only)  (authorize)     (dispatch)      (record)

### Why resolution comes before authorization

The design originally specified policy first. Implementing it exposed the flaw: risk
classification's second signal is *what the control says it does*, and a button named
"Transfer Funds" is dangerous whatever the action type claims. That signal requires the resolved
node, so authorizing first means authorizing half-blind.

This does not weaken the chokepoint, because the invariant was never "policy runs first". It is
**nothing reaches a surface without authorization**. Resolution is a pure function over a snapshot
that was already captured; it touches nothing, changes nothing, and cannot be observed by the
application. Running it first means policy decides knowing exactly what it is about to act on.

A resolution failure short-circuits: there is nothing to authorize, so policy is never consulted for
an action that cannot happen.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cua.domain.action import Action, ActionRisk
from cua.domain.actor import Actor
from cua.domain.capability import Capability
from cua.domain.result import FailureCode
from cua.domain.snapshot import UiSnapshot
from cua.domain.target import TargetDescriptor
from cua.evidence.bus import EventType, EvidenceBus
from cua.policy.engine import Outcome, PolicyDecision, PolicyEngine
from cua.policy.secrets import SecretResolver
from cua.surfaces.base import ActionResult, SurfaceDriver
from cua.targeting.resolver import Resolution, TargetResolutionError, TargetResolver


class DispatchStatus(StrEnum):
    OK = "ok"
    UNRESOLVED = "unresolved"
    """The target could not be resolved -- not found, or ambiguous and refused."""
    DENIED = "denied"
    NEEDS_CONFIRMATION = "needs_confirmation"
    FAILED = "failed"
    """Authorized and attempted, but the surface could not carry it out."""
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True)
class DispatchOutcome:
    """Everything that happened, in one value the caller can branch on."""

    status: DispatchStatus
    decision: PolicyDecision | None = None
    resolution: Resolution | None = None
    result: ActionResult | None = None
    drifted: bool = False
    failure_code: FailureCode | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status is DispatchStatus.OK


@dataclass
class Dispatcher:
    """Enforces the sequence. Holds the driver so nothing else has to."""

    driver: SurfaceDriver
    policy: PolicyEngine
    resolver: TargetResolver
    evidence: EvidenceBus
    secrets: SecretResolver | None = None

    def observe(self) -> UiSnapshot:
        """Capture the surface and record that we looked.

        Observation needs no authorization: it changes nothing. It is recorded anyway, because a
        failure is far easier to explain when you can see what the system perceived at each step --
        as opposed to what the page looked like.
        """
        snapshot = self.driver.observe()
        self.evidence.emit(
            EventType.OBSERVE,
            url=snapshot.url,
            title=snapshot.title,
            node_count=len(snapshot.nodes),
            http_status=snapshot.http_status,
            snapshot_ref=self.evidence.save_snapshot(snapshot, "observe"),
        )
        return snapshot

    def execute(
        self,
        action: Action,
        *,
        snapshot: UiSnapshot,
        actor: Actor,
        session_id: str,
        lease_epoch: int,
        expected_epoch: int | None = None,
        capability: Capability | None = None,
        declared_risk: ActionRisk | None = None,
        confirmed: bool = False,
    ) -> DispatchOutcome:
        """Resolve, authorize, dispatch, record. In that order, every time."""

        # --- 0. Is this actor still entitled to act? -------------------------
        # Escalation happens *while* a step is in flight, so an authorization minted a moment ago
        # may already be stale. Checking here -- before anything else -- means an automation step
        # cannot land on the page after a human has taken over and started typing.
        if expected_epoch is not None and lease_epoch != expected_epoch:
            self.evidence.emit(
                EventType.DISPATCH,
                actor=actor,
                lease_epoch=lease_epoch,
                ok=False,
                reason="stale lease epoch",
                expected_epoch=expected_epoch,
            )
            return DispatchOutcome(
                status=DispatchStatus.LEASE_LOST,
                failure_code=FailureCode.LEASE_LOST,
                message=f"lease epoch {lease_epoch} is stale (current {expected_epoch})",
            )

        # --- 1. Resolve (pure; no side effects on the surface) ---------------
        target: TargetDescriptor | None = getattr(action, "target", None)
        resolution: Resolution | None = None
        drifted = False

        if target is not None:
            try:
                resolution, drifted = self.resolver.resolve_with_drift(target, snapshot)
            except TargetResolutionError as failure:
                self.evidence.emit(
                    EventType.RESOLVE,
                    actor=actor,
                    lease_epoch=lease_epoch,
                    resolved=False,
                    target=target.describe(),
                    reason=failure.reason,
                    strategies_tried=[s.value for s in failure.strategies_tried],
                    candidates=list(failure.candidate_summaries()),
                    ambiguity=failure.ambiguity,
                )
                return DispatchOutcome(
                    status=DispatchStatus.UNRESOLVED,
                    failure_code=(
                        FailureCode.TARGET_AMBIGUOUS
                        if failure.is_ambiguous
                        else FailureCode.TARGET_NOT_FOUND
                    ),
                    message=str(failure),
                )

            self.evidence.emit(
                EventType.RESOLVE,
                actor=actor,
                lease_epoch=lease_epoch,
                resolved=True,
                target=target.describe(),
                node_id=resolution.node.node_id,
                strategy=resolution.strategy.value,
                candidates_considered=resolution.candidates_considered,
                drifted=drifted,
            )

        # --- 2. Authorize, now knowing what will be acted on -----------------
        decision = self.policy.authorize(
            action,
            actor=actor,
            session_id=session_id,
            lease_epoch=lease_epoch,
            target=resolution.node if resolution else None,
            capability=capability,
            declared_risk=declared_risk,
            confirmed=confirmed,
        )
        self.evidence.emit(
            EventType.AUTHORIZE,
            actor=actor,
            lease_epoch=lease_epoch,
            decision_id=decision.decision_id,
            granted=decision.allowed,
            outcome=decision.outcome.value,
            risk=decision.risk.tier.value,
            signals=list(decision.risk.signals),
            reason=decision.reason,
            action_type=action.type,
        )

        if decision.outcome is Outcome.REQUIRE_CONFIRMATION:
            return DispatchOutcome(
                status=DispatchStatus.NEEDS_CONFIRMATION,
                decision=decision,
                resolution=resolution,
                drifted=drifted,
                message=decision.reason,
            )

        if decision.authorized is None:
            return DispatchOutcome(
                status=DispatchStatus.DENIED,
                decision=decision,
                resolution=resolution,
                drifted=drifted,
                failure_code=(
                    FailureCode.NAVIGATION_BLOCKED
                    if "navigation blocked" in decision.reason
                    else FailureCode.POLICY_DENIED
                ),
                message=decision.reason,
            )

        # --- 3. Dispatch ------------------------------------------------------
        authorized = decision.authorized
        if resolution is not None:
            authorized = authorized.with_resolution(resolution.node.node_id)

        result = self.driver.dispatch(authorized)

        self.evidence.emit(
            EventType.DISPATCH,
            actor=actor,
            lease_epoch=lease_epoch,
            decision_id=decision.decision_id,
            action_type=action.type,
            node_id=authorized.resolved_node_id,
            ok=result.ok,
            navigated=result.navigated,
            http_status=result.http_status,
            duration_ms=result.duration_ms,
            message=result.message,
        )

        return DispatchOutcome(
            status=DispatchStatus.OK if result.ok else DispatchStatus.FAILED,
            decision=decision,
            resolution=resolution,
            result=result,
            drifted=drifted,
            failure_code=None if result.ok else FailureCode.ACTION_FAILED,
            message=result.message,
        )
