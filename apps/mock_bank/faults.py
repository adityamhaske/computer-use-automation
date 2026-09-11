"""Deterministic, flag-gated fault injection.

The brief asks for evidence of a replay hitting an error or exceptional state. That is the whole
reason this app is built rather than borrowed: a public demo site cannot be made to expire a session
on cue.

Two design rules, both load-bearing:

**Faults are armed explicitly and consumed deterministically.** `arm("transient_load", count=1)`
means the next request fails and the one after succeeds -- every time, in that order. There is no
randomness anywhere in this application. A flaky fixture would make every downstream failure
ambiguous, and "is this our bug or the fixture's?" is the question that eats debugging days.

**The control surface is not part of the application.** `/_control/*` is unlinked, unrendered, and
never appears in a UiSnapshot. The agent cannot find it, so it cannot cheat by disarming a fault.
The CLI arms faults out-of-band before a run starts.

Conditions that are *data* rather than faults -- member not found, account closed, no savings
account, permission denied -- live in `data.py`. That split is deliberate: those are legitimate
business outcomes the application produces on its own, and the capability must declare them. Faults
are the exceptional machinery on top.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Fault(StrEnum):
    """The exceptional states a replay must survive or report."""

    TRANSIENT_LOAD = "transient_load"
    """A 502 from the app server. Recoverable: declared remedy is reload with backoff."""

    SESSION_TIMEOUT = "session_timeout"
    """The session silently expires; the next request lands on the login screen.
    Recoverable if the capability declares a re-auth rule; otherwise it escalates."""

    UNDECLARED_DIALOG = "undeclared_dialog"
    """An interstitial notice that NO capability declares -- the point is that it is unknown.
    This must produce UNEXPECTED_STATE and fail closed, not be clicked through."""

    VALIDATION_ERROR = "validation_error"
    """The form rejects a submission with a field-level error.
    A business outcome, not a crash: the caller needs to know the request was rejected."""


@dataclass
class FaultState:
    """Armed faults and their remaining trigger counts.

    In-memory and process-local. This is a test fixture, not infrastructure -- see AGENTS.md on
    scope discipline.
    """

    armed: dict[str, int] = field(default_factory=dict)

    def arm(self, fault: str, count: int = 1) -> None:
        """Arm `fault` to trigger on the next `count` eligible requests.

        `count=-1` arms it indefinitely, which is how the RECOVERY_EXHAUSTED path is exercised: a
        'transient' condition that never clears must eventually stop being retried.
        """
        if fault not in set(Fault):
            raise ValueError(f"unknown fault {fault!r}; expected one of {[f.value for f in Fault]}")
        self.armed[fault] = count

    def should_trigger(self, fault: Fault) -> bool:
        """Consume one trigger of `fault` if armed. Deterministic: no randomness, no timing."""
        remaining = self.armed.get(fault.value, 0)
        if remaining == 0:
            return False
        if remaining > 0:
            self.armed[fault.value] = remaining - 1
        return True

    def reset(self) -> None:
        self.armed.clear()

    def snapshot(self) -> dict[str, int]:
        """Current state, for assertions in tests."""
        return dict(self.armed)
