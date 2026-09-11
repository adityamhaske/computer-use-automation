"""The observation taxonomy and result contract behave as designed.

The distinction these tests defend is the one the brief calls out as most commonly got wrong: a
business outcome is a legitimate answer, not a failure.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cua.domain.result import (
    ESCALATABLE,
    BusinessOutcome,
    FailureCode,
    FailureDetail,
    InterventionRef,
    ObservationClass,
    ResolutionDebug,
    RunResult,
    RunStatus,
)

BASE = {
    "capability_id": "corebank.member.savings_balance",
    "capability_version": "1.0.0",
    "run_id": "run-1",
}


def test_business_outcome_exits_zero() -> None:
    """A negative answer is still a successful execution.

    Treating "no such member" as a failure turns a routine result into a page at 2am, and trains
    everyone to ignore the alert that also fires for real defects.
    """
    result = RunResult(
        status=RunStatus.BUSINESS_OUTCOME,
        outcome=BusinessOutcome(code="member_not_found", data={"member_id": "99999"}),
        **BASE,
    )
    assert result.exit_code == 0
    assert result.outcome is not None and result.outcome.code == "member_not_found"


def test_failure_exits_one_and_escalation_exits_two() -> None:
    """Three distinguishable states for a caller and a shell alike: fine, broken, needs a person."""
    failed = RunResult(
        status=RunStatus.FAILED,
        error=FailureDetail(code=FailureCode.CHECKPOINT_FAILED, message="nope"),
        **BASE,
    )
    escalated = RunResult(
        status=RunStatus.NEEDS_HUMAN,
        intervention=InterventionRef(intervention_id="i-1", reason="undeclared screen"),
        **BASE,
    )
    assert failed.exit_code == 1
    assert escalated.exit_code == 2


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"status": RunStatus.FAILED}, "FAILED without an error"),
        ({"status": RunStatus.BUSINESS_OUTCOME}, "BUSINESS_OUTCOME without an outcome"),
        ({"status": RunStatus.NEEDS_HUMAN}, "NEEDS_HUMAN without an intervention"),
        (
            {
                "status": RunStatus.SUCCESS,
                "error": FailureDetail(code=FailureCode.INTERNAL, message="x"),
            },
            "SUCCESS carrying an error",
        ),
    ],
)
def test_result_shape_is_enforced_not_merely_documented(payload: dict, why: str) -> None:
    with pytest.raises(ValidationError):
        RunResult(**payload, **BASE)  # type: ignore[arg-type]


def test_failure_carries_enough_to_debug_without_reproducing() -> None:
    """Brief §3.3: a failure must say what step, what was expected, what was observed."""
    detail = FailureDetail(
        code=FailureCode.TARGET_AMBIGUOUS,
        message="2 candidates matched",
        step_id="submit_search",
        expected='exactly one button named "Search"',
        observed="2 matching nodes in frame content",
        resolution=ResolutionDebug(
            target_description='button named exact "Search"',
            candidates_considered=2,
            ambiguity_score=0.97,
        ),
    )
    assert detail.step_id and detail.expected and detail.observed
    assert detail.resolution is not None and detail.resolution.candidates_considered == 2


def test_only_human_resolvable_failures_escalate() -> None:
    """Handing an operator a live session helps for an ambiguous control or an unrecognized screen.

    It does not help for an internal error, a dead browser, or a caller passing a malformed member
    id -- routing those to a person would be noise, and noise is how escalation queues get ignored.
    """
    assert FailureCode.TARGET_AMBIGUOUS in ESCALATABLE
    assert FailureCode.UNEXPECTED_STATE in ESCALATABLE
    assert FailureCode.CHECKPOINT_FAILED in ESCALATABLE

    assert FailureCode.INTERNAL not in ESCALATABLE
    assert FailureCode.SURFACE_UNAVAILABLE not in ESCALATABLE
    assert FailureCode.INPUT_VALIDATION_FAILED not in ESCALATABLE


def test_recoverable_is_an_observation_never_a_terminal_status() -> None:
    """RECOVERABLE describes a moment, not an ending. A result contract where "retrying" is a
    terminal state cannot be consumed sensibly."""
    assert ObservationClass.RECOVERABLE in set(ObservationClass)
    assert "recoverable" not in {s.value for s in RunStatus}


def test_unexpected_state_exists_in_both_families() -> None:
    """The category most systems lack: a screen the capability does not understand.

    It is an observation (what we saw) and a failure code (how we stopped), because failing closed
    on it is a decision the caller needs to see.
    """
    assert ObservationClass.UNEXPECTED_STATE in set(ObservationClass)
    assert FailureCode.UNEXPECTED_STATE in set(FailureCode)
    assert FailureCode.UNEXPECTED_STATE in ESCALATABLE
