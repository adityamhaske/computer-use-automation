"""Who is allowed to act on the live session, right now.

The brief asks for something specific and easy to fake: a human must take control of **the same live
session** the automation was using, do the manual work, and hand it back. The lease is what makes
"who is in control" a fact the system can check rather than a convention it hopes holds.

### Why the epoch exists

Escalation happens *while a step is in flight* -- that is usually what "stuck" means. Without a
generation counter, an automation step that was already dispatched can land on the page a moment
after the operator has taken over and started typing. The two writers then interleave on a member's
record, and the evidence shows both as legitimate.

Every transition increments the epoch, and every dispatch carries the epoch it was authorized
under. A dispatch whose epoch is stale is refused with `LEASE_LOST`. That is not an error the
executor recovers from; it is the system noticing that the world moved underneath an action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from cua.domain.actor import Actor


class ControlState(StrEnum):
    """Who holds the session, and what is expected to happen next."""

    RUNNING = "running"
    """Automation is executing normally."""

    PAUSED = "paused"
    """Automation has stopped and asked for a human. Nobody is acting."""

    HUMAN_CONTROL = "human_control"
    """An operator has claimed the session and is working in it."""

    RESUMING = "resuming"
    """The operator released it; the executor is reconciling state before continuing."""


class LeaseError(RuntimeError):
    """An action was attempted by an actor that does not hold the lease, or at a stale epoch."""


@dataclass
class Lease:
    """A single-writer guarantee over one live session."""

    session_id: str
    holder: Actor = Actor.AUTOMATION
    state: ControlState = ControlState.RUNNING
    epoch: int = 1
    """Monotonic. Incremented on every transition, never reset."""

    operator: str | None = None
    expires_at: datetime | None = None
    """When a human's hold lapses.

    A stuck automation is recoverable -- a person notices. A session held open by an operator who
    walked away is not, so the hold has an expiry rather than trusting that release always happens.
    """

    history: list[str] = field(default_factory=list)

    def held_by(self, actor: Actor, epoch: int) -> bool:
        return self.holder is actor and self.epoch == epoch and not self.expired

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and datetime.now(UTC) >= self.expires_at

    def assert_held(self, actor: Actor, epoch: int) -> None:
        """Raise unless this actor may act right now, at this generation."""
        if self.expired:
            raise LeaseError(
                f"the {self.holder.value} hold on session {self.session_id} expired at "
                f"{self.expires_at}"
            )
        if self.holder is not actor:
            raise LeaseError(
                f"{actor.value} tried to act while {self.holder.value} holds session "
                f"{self.session_id}"
            )
        if self.epoch != epoch:
            raise LeaseError(
                f"stale lease epoch {epoch} (current {self.epoch}): control changed hands since "
                "this action was authorized"
            )

    # ------------------------------------------------------------ transitions

    def _transition(self, *, holder: Actor, state: ControlState, note: str) -> int:
        self.holder = holder
        self.state = state
        self.epoch += 1
        self.history.append(f"{self.epoch}:{state.value}:{note}")
        return self.epoch

    def escalate(self, reason: str) -> int:
        """Automation stops and asks for a human. Nobody holds the session in between.

        Handing straight to `HUMAN_CONTROL` would be simpler and wrong: there may be no operator,
        and a session nominally held by an absent human is a session nothing can recover.
        """
        if self.state is not ControlState.RUNNING:
            raise LeaseError(f"cannot escalate from {self.state.value}")
        self.operator = None
        self.expires_at = None
        return self._transition(holder=Actor.SYSTEM, state=ControlState.PAUSED, note=reason)

    def claim(self, operator: str, *, hold_for: timedelta = timedelta(minutes=30)) -> int:
        """An operator takes the session."""
        if self.state is not ControlState.PAUSED:
            raise LeaseError(f"cannot claim a session that is {self.state.value}")
        self.operator = operator
        self.expires_at = datetime.now(UTC) + hold_for
        return self._transition(
            holder=Actor.HUMAN, state=ControlState.HUMAN_CONTROL, note=f"claimed by {operator}"
        )

    def release(self) -> int:
        """The operator hands the session back. The executor must reconcile before continuing."""
        if self.state is not ControlState.HUMAN_CONTROL:
            raise LeaseError(f"cannot release a session that is {self.state.value}")
        self.expires_at = None
        return self._transition(
            holder=Actor.SYSTEM,
            state=ControlState.RESUMING,
            note=f"released by {self.operator or 'operator'}",
        )

    def resume(self) -> int:
        """Automation takes the session back, after reconciliation."""
        if self.state is not ControlState.RESUMING:
            raise LeaseError(f"cannot resume from {self.state.value}")
        self.operator = None
        return self._transition(holder=Actor.AUTOMATION, state=ControlState.RUNNING, note="resumed")

    def reclaim_expired(self) -> int:
        """Take back a hold that lapsed.

        Returns the session to PAUSED rather than to RUNNING: the operator may have left the
        application in any state at all, so the next actor still has to reconcile before acting.
        """
        if not self.expired:
            raise LeaseError("the lease has not expired")
        self.operator = None
        self.expires_at = None
        return self._transition(holder=Actor.SYSTEM, state=ControlState.PAUSED, note="hold expired")
