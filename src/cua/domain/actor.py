"""Who is acting.

Every action in this system carries an actor. That is not bookkeeping -- it is what makes the
human-in-the-loop path auditable rather than a hole in the model. A human operating an escalated
session goes through the same authorization path as automation, under a different policy profile,
and every action they take is recorded with `actor=HUMAN`.
"""

from __future__ import annotations

from enum import StrEnum


class Actor(StrEnum):
    """The originator of an action."""

    AUTOMATION = "automation"
    """The discovery agent or the replay executor."""

    HUMAN = "human"
    """A human operator who has claimed the lease during an intervention.

    Broader authority than AUTOMATION -- a human may confirm an irreversible action, which is the
    entire point of escalating -- but bound by the same allowlist and the same evidence trail.
    """

    SYSTEM = "system"
    """The runtime itself: lease transitions, evidence capture, snapshot observation.

    Never dispatches a state-changing action.
    """
