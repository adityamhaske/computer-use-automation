"""What a run observed, and how it ended.

Two type families, deliberately separate. The brief names conflating a business outcome with a
failure as "the most common design mistake here", and the usual cause is a single enum trying to
answer two different questions:

    ObservationClass   what does this screen mean?        (a classification of a moment)
    RunStatus          how did the run terminate?         (the caller's contract)

`RECOVERABLE` belongs to the first and not the second: it is something you *see*, never a way a run
*ends*. Mixing them produces a result contract where "retrying" is a terminal state.

See ADR 0003 and docs/design/error-taxonomy.md.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cua.domain.target import ResolutionStrategy


class ObservationClass(StrEnum):
    """What the executor decided a screen means."""

    EXPECTED = "expected"
    """Satisfies a precondition or checkpoint. Proceed."""

    BUSINESS_OUTCOME = "business_outcome"
    """Matches a declared outcome: a legitimate answer the caller needs.
    "No such member" is not a crash."""

    RECOVERABLE = "recoverable"
    """Matches a declared recovery rule. Remediate and retry, bounded by `max_attempts`.
    NOT terminal -- exhausting attempts converts it to RECOVERY_EXHAUSTED."""

    UNEXPECTED_STATE = "unexpected_state"
    """Matches nothing the capability declares.

    The category most systems lack, and the one that matters most. Without it, an unrecognized
    screen gets forced into one of the others -- and the tempting choice is "keep going and see",
    which is how an automation acts on a page it does not understand. Here it fails closed.
    """


class RunStatus(StrEnum):
    """How a run terminated. The caller's contract."""

    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    NEEDS_HUMAN = "needs_human"
    """First-class and terminal. Escalation is a normal way for a run to end, which is what makes
    the human-in-the-loop path architectural rather than an exception handler."""
    FAILED = "failed"


class FailureCode(StrEnum):
    """Why a run failed. Each is independently actionable."""

    TARGET_NOT_FOUND = "target_not_found"
    TARGET_AMBIGUOUS = "target_ambiguous"
    """Several candidates matched and the resolver refused to choose.
    A refusal, not a defect in itself -- and far cheaper than a wrong click."""
    PRECONDITION_FAILED = "precondition_failed"
    CHECKPOINT_FAILED = "checkpoint_failed"
    ACTION_FAILED = "action_failed"
    TIMEOUT = "timeout"
    NAVIGATION_BLOCKED = "navigation_blocked"
    """An allowlist violation. Also the signal a prompt injection would trip."""
    POLICY_DENIED = "policy_denied"
    SURFACE_UNAVAILABLE = "surface_unavailable"
    RECOVERY_EXHAUSTED = "recovery_exhausted"
    INPUT_VALIDATION_FAILED = "input_validation_failed"
    """The caller's arguments failed the declared schema -- caught before touching the UI."""
    UNEXPECTED_STATE = "unexpected_state"
    LEASE_LOST = "lease_lost"
    """Dispatched with a stale lease epoch: automation tried to act after a human took over."""
    INTERNAL = "internal"


ESCALATABLE: frozenset[FailureCode] = frozenset(
    {
        FailureCode.TARGET_NOT_FOUND,
        FailureCode.TARGET_AMBIGUOUS,
        FailureCode.CHECKPOINT_FAILED,
        FailureCode.RECOVERY_EXHAUSTED,
        FailureCode.UNEXPECTED_STATE,
        FailureCode.POLICY_DENIED,
    }
)
"""Failures a human could plausibly resolve by taking over the session.

The rest -- an internal error, a dead browser, a caller passing a malformed member id -- are not
improved by handing someone a live session, and routing them to an operator would be noise.
"""


class ResolutionDebug(BaseModel):
    """Why targeting produced the answer it did.

    Included in every failure because the brief requires a failure be debuggable without
    reproducing it, and "could not find the button" is not.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_description: str
    strategies_tried: tuple[ResolutionStrategy, ...] = ()
    candidates_considered: int = 0
    candidate_summaries: tuple[str, ...] = ()
    ambiguity_score: float | None = None


class FailureDetail(BaseModel):
    """A failure in the terms the brief asks for: what step, what was expected, what was seen."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: FailureCode
    message: str
    step_id: str | None = None
    expected: str | None = None
    observed: str | None = None
    resolution: ResolutionDebug | None = None

    @property
    def is_escalatable(self) -> bool:
        return self.code in ESCALATABLE


class BusinessOutcome(BaseModel):
    """A declared, legitimate result of the business process."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    data: dict[str, Any] = Field(default_factory=dict)


class InterventionRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intervention_id: str
    reason: str
    step_id: str | None = None

    step_index: int | None = None
    """Which step stopped, by position.

    `step_id` names it, but a resume needs to scan forward *from* it, and nothing else in the
    system maps an id back to an index. Carrying the number is what lets a caller hand the run
    back to the executor without re-deriving position by string matching -- and scanning from 0
    instead is actively wrong once a flow has advanced, because the early steps' preconditions no
    longer hold.
    """

    outputs_so_far: dict[str, str] = Field(default_factory=dict)
    """What the run had already extracted when it stopped.

    A NEEDS_HUMAN result used to return nothing here, so everything read before the escalation was
    discarded and a resumed run would have to re-read it -- or, worse, complete with outputs the
    caller never received.
    """


class RunResult(BaseModel):
    """What the caller gets back.

    One model rather than a union, because the primary caller is an AI agent consuming JSON and a
    four-way union is awkward to handle there. Consistency is enforced by validation instead, so
    the convenience does not cost correctness.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: RunStatus
    capability_id: str
    capability_version: str
    run_id: str

    outputs: dict[str, Any] = Field(default_factory=dict)
    outcome: BusinessOutcome | None = None
    error: FailureDetail | None = None
    intervention: InterventionRef | None = None

    steps_executed: int = 0
    steps_total: int = 0
    duration_ms: int = 0
    drift_score: float = 0.0
    """0.0 means every target resolved exactly as recorded. Rising values mean the surface is
    moving under the artifact -- visible before replays begin to fail."""
    strategy_mix: dict[str, int] = Field(default_factory=dict)
    recovery_attempts: int = 0
    evidence_ref: str | None = None

    @model_validator(mode="after")
    def _shape_matches_status(self) -> Self:
        """Each status carries exactly the payload its contract promises."""
        if self.status is RunStatus.BUSINESS_OUTCOME and self.outcome is None:
            raise ValueError("BUSINESS_OUTCOME requires `outcome`")
        if self.status is RunStatus.FAILED and self.error is None:
            raise ValueError("FAILED requires `error`")
        if self.status is RunStatus.NEEDS_HUMAN and self.intervention is None:
            raise ValueError("NEEDS_HUMAN requires `intervention`")
        if self.status is RunStatus.SUCCESS and self.error is not None:
            raise ValueError("SUCCESS cannot carry an error")
        return self

    @property
    def exit_code(self) -> int:
        """Process exit code.

        BUSINESS_OUTCOME exits 0 on purpose. "No such member" is a successful execution that
        returned a negative answer; treating it as a failure turns a routine result into a 2am page
        and trains everyone to ignore the alert that also fires for real defects.
        """
        return {
            RunStatus.SUCCESS: 0,
            RunStatus.BUSINESS_OUTCOME: 0,
            RunStatus.FAILED: 1,
            RunStatus.NEEDS_HUMAN: 2,
        }[self.status]

    @property
    def summary(self) -> str:
        """One line, for a CLI and for a calling agent's log."""
        if self.status is RunStatus.SUCCESS:
            return f"SUCCESS - {len(self.outputs)} output(s) in {self.duration_ms}ms"
        if self.status is RunStatus.BUSINESS_OUTCOME:
            assert self.outcome is not None
            return f"BUSINESS OUTCOME - {self.outcome.code}"
        if self.status is RunStatus.NEEDS_HUMAN:
            assert self.intervention is not None
            return f"NEEDS HUMAN - {self.intervention.reason}"
        assert self.error is not None
        where = f" at step {self.error.step_id}" if self.error.step_id else ""
        return f"FAILED - {self.error.code.value}{where}: {self.error.message}"
