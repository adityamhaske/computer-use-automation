"""Arbitrating control of one live session.

The broker owns the lease and the intervention queue, and it is the only way a human's input reaches
the application. That last point is the whole design.

### Why the console does not inject input

The obvious implementation of an operator console -- stream the page to a human, forward their
clicks straight into it over CDP -- means that the moment a person takes over, the allowlist, the
risk classification and the audit trail all stop applying. Escalation would become a hole in the
security model, at exactly the moment a regulated system most needs one.

So the console submits `raw_input` **actions** to this broker, and the broker runs them through the
same `Action -> TargetResolver -> PolicyEngine -> SurfaceDriver` path automation uses, under the
`HUMAN` policy profile. An operator may do things automation may not -- confirm an irreversible
action, navigate off the recorded flow -- and every one of those is authorized, classified and
recorded. Escalation *widens authority auditably*; it does not disable the chokepoint.

"Record what the human did" is therefore not a feature that was built. It is a consequence of the
path, and it arrives in the same shape as everything else, tagged `actor=HUMAN`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from cua.domain.action import Action, RawInput
from cua.domain.actor import Actor
from cua.domain.snapshot import UiSnapshot
from cua.evidence.bus import EventType, EvidenceBus
from cua.hitl.intervention import InterventionQueue, InterventionRequest
from cua.hitl.lease import ControlState, Lease, LeaseError
from cua.hitl.reanchor import HumanDelta, diff_snapshots


@dataclass
class HandoffRecord:
    """Everything that happened while a human held the session."""

    intervention_id: str
    operator: str
    actions: int = 0
    delta: HumanDelta | None = None
    snapshot_before: UiSnapshot | None = None
    snapshot_after: UiSnapshot | None = None


@dataclass
class SessionBroker:
    """Owns who may act on a session, and routes a human's actions through policy."""

    session_id: str
    evidence: EvidenceBus
    dispatch: object
    """The `Dispatcher`. Typed loosely because `cua.hitl` sits below `cua.runtime` in the layering
    and may not import it -- the broker calls it, it does not depend on its type."""

    lease: Lease = field(init=False)
    queue: InterventionQueue = field(default_factory=InterventionQueue)
    handoff: HandoffRecord | None = None

    def __post_init__(self) -> None:
        self.lease = Lease(session_id=self.session_id)

    # ------------------------------------------------------------- escalate

    def escalate(
        self,
        *,
        run_id: str,
        capability_ref: str,
        goal: str,
        reason: str,
        step_id: str | None = None,
        failure_code: str | None = None,
        snapshot: UiSnapshot | None = None,
        snapshot_ref: str | None = None,
        screenshot_ref: str | None = None,
    ) -> InterventionRequest:
        """Stop, and ask for a person."""
        epoch = self.lease.escalate(reason)
        request = self.queue.open(
            InterventionRequest(
                intervention_id=f"int-{uuid.uuid4().hex[:10]}",
                session_id=self.session_id,
                run_id=run_id,
                capability_ref=capability_ref,
                goal=goal,
                reason=reason,
                step_id=step_id,
                failure_code=failure_code,
                snapshot_ref=snapshot_ref,
                screenshot_ref=screenshot_ref,
                evidence_ref=str(self.evidence.run_dir),
            )
        )
        self._pending_before = snapshot
        self.evidence.emit(
            EventType.ESCALATE,
            actor=Actor.SYSTEM,
            lease_epoch=epoch,
            intervention=request.intervention_id,
            reason=reason,
            step=step_id,
            failure_code=failure_code,
        )
        return request

    # ---------------------------------------------------------------- claim

    def claim(
        self, intervention_id: str, operator: str, *, hold_for: timedelta = timedelta(minutes=30)
    ) -> int:
        """An operator takes the session. Returns the new lease epoch.

        Any automation action authorized before this point is now stale and will be refused -- which
        is the race this whole mechanism exists to kill.
        """
        request = self.queue.claim(intervention_id, operator)
        epoch = self.lease.claim(operator, hold_for=hold_for)
        self.handoff = HandoffRecord(
            intervention_id=request.intervention_id,
            operator=operator,
            snapshot_before=getattr(self, "_pending_before", None),
        )
        self.evidence.emit(
            EventType.LEASE,
            actor=Actor.HUMAN,
            lease_epoch=epoch,
            transition="claimed",
            operator=operator,
            intervention=intervention_id,
        )
        return epoch

    # ------------------------------------------------------- the policed path

    def human_action(self, action: Action) -> object:
        """Perform one operator action -- through policy, like everything else.

        A human's input is not privileged. It is `actor=HUMAN`, which is a *different policy
        profile*, not an absent one.
        """
        if self.lease.state is not ControlState.HUMAN_CONTROL:
            raise LeaseError(
                f"no operator holds session {self.session_id} (state: {self.lease.state.value})"
            )
        self.lease.assert_held(Actor.HUMAN, self.lease.epoch)

        if isinstance(action, RawInput) and self.handoff is None:
            raise LeaseError("raw input requires an active handoff")

        outcome = self.dispatch.execute(  # type: ignore[attr-defined]
            action,
            snapshot=self.dispatch.observe(),  # type: ignore[attr-defined]
            actor=Actor.HUMAN,
            session_id=self.session_id,
            lease_epoch=self.lease.epoch,
            expected_epoch=self.lease.epoch,
        )
        if self.handoff is not None:
            self.handoff.actions += 1
        return outcome

    def confirm_action(self, action: Action) -> object:
        """An operator action that policy requires them to confirm explicitly.

        The confirmation is the *point*: an irreversible action automation may never take is one a
        person can take, having been shown what it is and said yes. Recorded as such.
        """
        self.lease.assert_held(Actor.HUMAN, self.lease.epoch)
        outcome = self.dispatch.execute(  # type: ignore[attr-defined]
            action,
            snapshot=self.dispatch.observe(),  # type: ignore[attr-defined]
            actor=Actor.HUMAN,
            session_id=self.session_id,
            lease_epoch=self.lease.epoch,
            expected_epoch=self.lease.epoch,
            confirmed=True,
        )
        if self.handoff is not None:
            self.handoff.actions += 1
        self.evidence.emit(
            EventType.NOTE,
            actor=Actor.HUMAN,
            lease_epoch=self.lease.epoch,
            note="operator confirmed an action policy would not permit automation to take",
            action_type=action.type,
        )
        return outcome

    # -------------------------------------------------------------- release

    def release(self, snapshot_after: UiSnapshot | None = None) -> HumanDelta | None:
        """The operator hands back. Records what changed while they held it."""
        if self.handoff is None:
            raise LeaseError("no handoff is in progress")

        delta = None
        if self.handoff.snapshot_before is not None and snapshot_after is not None:
            delta = diff_snapshots(self.handoff.snapshot_before, snapshot_after)
            self.handoff.delta = delta
            self.handoff.snapshot_after = snapshot_after

        epoch = self.lease.release()
        self.queue.release(
            self.handoff.intervention_id,
            human_actions=self.handoff.actions,
            human_delta=delta.summary() if delta else "not captured",
        )
        self.evidence.emit(
            EventType.LEASE,
            actor=Actor.HUMAN,
            lease_epoch=epoch,
            transition="released",
            operator=self.handoff.operator,
            human_actions=self.handoff.actions,
            human_delta=delta.summary() if delta else None,
            values_changed=[node for node, _, _ in (delta.values_changed if delta else ())],
        )
        return delta

    def resume(self) -> int:
        """Automation takes the session back. Returns the epoch it must now act under."""
        epoch = self.lease.resume()
        if self.handoff is not None:
            self.queue.resolve(self.handoff.intervention_id, "automation resumed")
        self.evidence.emit(
            EventType.LEASE, actor=Actor.AUTOMATION, lease_epoch=epoch, transition="resumed"
        )
        return epoch
