"""Routing a stuck run to a person, with enough context to act on.

An intervention request is what an operator sees. It has to answer, without them opening a log:
what was this trying to do, where did it stop, why, and what does the screen look like now.

The contents are deliberately constrained. Everything here has already passed through the redactor,
because an intervention queue is a sink like any other -- arguably the most exposed one, since it is
the artifact a human actually reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class InterventionState(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    RELEASED = "released"
    """The operator handed the session back; the executor is reconciling."""
    RESOLVED = "resolved"
    ABANDONED = "abandoned"
    """Nobody took it, or the hold expired. Distinguished from RESOLVED so that a queue that is
    quietly failing looks different from one that is working."""


@dataclass
class InterventionRequest:
    """One request for a human to take over a live session."""

    intervention_id: str
    session_id: str
    run_id: str
    capability_ref: str
    goal: str
    reason: str
    """Why automation stopped, in the words the executor used -- an ambiguous control, an
    unrecognized screen, an irreversible action needing a decision."""

    step_id: str | None = None
    failure_code: str | None = None
    snapshot_ref: str | None = None
    screenshot_ref: str | None = None
    evidence_ref: str | None = None

    state: InterventionState = InterventionState.OPEN
    operator: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    claimed_at: datetime | None = None
    released_at: datetime | None = None
    human_actions: int = 0
    human_delta: str = ""
    resolution_note: str = ""

    def context_card(self) -> dict[str, str | None]:
        """What an operator is shown. Everything here is already redacted."""
        return {
            "intervention": self.intervention_id,
            "capability": self.capability_ref,
            "goal": self.goal,
            "stopped_at": self.step_id,
            "because": self.reason,
            "screenshot": self.screenshot_ref,
            "snapshot": self.snapshot_ref,
            "evidence": self.evidence_ref,
        }


@dataclass
class InterventionQueue:
    """In-memory queue of open interventions.

    In-memory because this is a single-process system by design (AGENTS.md scope discipline). The
    seam to a durable queue is this class: nothing outside it knows how requests are stored.
    """

    requests: dict[str, InterventionRequest] = field(default_factory=dict)

    def open(self, request: InterventionRequest) -> InterventionRequest:
        self.requests[request.intervention_id] = request
        return request

    def get(self, intervention_id: str) -> InterventionRequest | None:
        return self.requests.get(intervention_id)

    def pending(self) -> list[InterventionRequest]:
        """Open requests, oldest first -- a queue, not a stack.

        A stuck run that nobody looked at for an hour is more urgent than one that just stopped,
        and newest-first ordering is how the oldest item never gets picked up.
        """
        return sorted(
            (r for r in self.requests.values() if r.state is InterventionState.OPEN),
            key=lambda r: r.created_at,
        )

    def claim(self, intervention_id: str, operator: str) -> InterventionRequest:
        request = self._require(intervention_id)
        if request.state is not InterventionState.OPEN:
            raise ValueError(f"intervention {intervention_id} is {request.state.value}, not open")
        request.state = InterventionState.CLAIMED
        request.operator = operator
        request.claimed_at = datetime.now(UTC)
        return request

    def release(
        self, intervention_id: str, *, human_actions: int, human_delta: str
    ) -> InterventionRequest:
        request = self._require(intervention_id)
        if request.state is not InterventionState.CLAIMED:
            raise ValueError(f"intervention {intervention_id} is not claimed")
        request.state = InterventionState.RELEASED
        request.released_at = datetime.now(UTC)
        request.human_actions = human_actions
        request.human_delta = human_delta
        return request

    def resolve(self, intervention_id: str, note: str = "") -> InterventionRequest:
        request = self._require(intervention_id)
        request.state = InterventionState.RESOLVED
        request.resolution_note = note
        return request

    def abandon(self, intervention_id: str, note: str = "") -> InterventionRequest:
        request = self._require(intervention_id)
        request.state = InterventionState.ABANDONED
        request.resolution_note = note
        return request

    def _require(self, intervention_id: str) -> InterventionRequest:
        request = self.requests.get(intervention_id)
        if request is None:
            raise KeyError(f"no intervention {intervention_id!r}")
        return request
