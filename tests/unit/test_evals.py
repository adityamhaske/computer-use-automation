"""The scorers must be right before the numbers they produce mean anything.

An eval harness is measuring equipment. A miscalibrated one is worse than none, because it produces
confident numbers that nobody re-derives -- both bugs these tests pin were found that way: a suite
reported four cases non-deterministic when replay had decided identically every time, and a second
reported extra steps that never happened.
"""

from __future__ import annotations

from cua.domain.result import BusinessOutcome, FailureCode, FailureDetail, RunResult, RunStatus
from cua.domain.run_record import ResolutionRecord, RunKind, RunRecord, StepRecord
from cua.domain.target import ResolutionStrategy
from cua.evals import scorers
from cua.evals.scorers import GroundTruth, truth_key

REF = "corebank.member.savings_balance@1.0.0"


def _record(
    *,
    inputs: dict[str, str],
    status: RunStatus = RunStatus.SUCCESS,
    outputs: dict[str, str] | None = None,
    outcome: str | None = None,
    error: FailureCode | None = None,
    node_id: str = "content:/row[2]/cell[1]",
    strategy: ResolutionStrategy = ResolutionStrategy.SEMANTIC_EXACT,
    recorded: ResolutionStrategy = ResolutionStrategy.SEMANTIC_EXACT,
    authorized: bool = True,
) -> RunRecord:
    result = RunResult(
        status=status,
        capability_id="corebank.member.savings_balance",
        capability_version="1.0.0",
        run_id="r",
        outputs=outputs or {},
        outcome=BusinessOutcome(code=outcome) if outcome else None,
        error=(
            FailureDetail(
                code=error,
                step_id="read_balance",
                message="refused",
                expected="x",
                observed="y",
            )
            if error
            else None
        ),
    )
    return RunRecord(
        run_id="r",
        kind=RunKind.REPLAY,
        capability_ref=REF,
        inputs=dict(inputs),
        result=result,
        steps=(
            StepRecord(
                index=0,
                step_id="read_balance",
                action_type="extract",
                authorized=authorized,
                resolution=ResolutionRecord(
                    target_description="cell beside Savings Balance",
                    strategy_used=strategy,
                    strategy_recorded=recorded,
                    resolved_node_id=node_id,
                ),
            ),
        ),
    )


TRUTH = {
    truth_key({"member_id": "12345"}): GroundTruth(
        inputs={"member_id": "12345"},
        expect_status=RunStatus.SUCCESS,
        outputs={"savings_balance": "$4,210.55"},
    ),
    truth_key({"member_id": "99999"}): GroundTruth(
        inputs={"member_id": "99999"},
        expect_status=RunStatus.BUSINESS_OUTCOME,
        outcome_code="member_not_found",
    ),
}


# --------------------------------------------------------- the headline metric


def test_a_correct_run_is_not_a_wrong_action() -> None:
    good = _record(inputs={"member_id": "12345"}, outputs={"savings_balance": "$4,210.55"})
    assert scorers.wrong_action_count([good], TRUTH) == 0


def test_reading_another_members_balance_is_a_wrong_action() -> None:
    """The failure this whole system is shaped to prevent: confident, successful, and wrong."""
    wrong = _record(inputs={"member_id": "12345"}, outputs={"savings_balance": "$18,730.00"})
    assert scorers.wrong_action_count([wrong], TRUTH) == 1


def test_the_wrong_business_outcome_is_a_wrong_action() -> None:
    wrong = _record(
        inputs={"member_id": "99999"},
        status=RunStatus.BUSINESS_OUTCOME,
        outcome="account_closed",
    )
    assert scorers.wrong_action_count([wrong], TRUTH) == 1


def test_refusing_is_never_a_wrong_action() -> None:
    """Refusal rate and wrong-action rate are different numbers and must never be traded off.

    A system tuned to refuse less eventually acts on a control it was not sure about.
    """
    refused = _record(
        inputs={"member_id": "12345"},
        status=RunStatus.FAILED,
        error=FailureCode.TARGET_AMBIGUOUS,
    )
    assert scorers.wrong_action_count([refused], TRUTH) == 0
    assert scorers.refusal_count([refused]) == 1


def test_a_business_outcome_counts_toward_success() -> None:
    """ "No such member" is the capability working and returning a negative answer."""
    outcome = _record(
        inputs={"member_id": "99999"},
        status=RunStatus.BUSINESS_OUTCOME,
        outcome="member_not_found",
    )
    assert scorers.success_rate([outcome]) == 1.0


# ------------------------------------------------------------------ determinism


def test_determinism_is_judged_per_input_not_across_the_suite() -> None:
    """The regression that matters here.

    Different members are *supposed* to decide differently -- 12345 extracts a balance, 99999
    returns a business outcome. Comparing every run in a suite against one another reported a
    perfectly deterministic system as non-deterministic, which would have sent someone hunting a
    bug that was in the measurement.
    """
    records = [
        _record(inputs={"member_id": "12345"}, outputs={"savings_balance": "$4,210.55"}),
        _record(
            inputs={"member_id": "99999"},
            status=RunStatus.BUSINESS_OUTCOME,
            outcome="member_not_found",
        ),
    ]
    assert scorers.determinism_holds(records) is True
    assert scorers.nondeterministic_cases(records) == []


def test_the_same_inputs_deciding_differently_is_caught() -> None:
    records = [
        _record(inputs={"member_id": "12345"}, node_id="content:/row[2]/cell[1]"),
        _record(inputs={"member_id": "12345"}, node_id="content:/row[9]/cell[1]"),
    ]
    assert scorers.determinism_holds(records) is False
    assert scorers.nondeterministic_cases(records) == ["member_id=12345"]


def test_timings_and_run_ids_are_excluded_from_the_decision_trace() -> None:
    """Determinism is about which control was chosen and why, not about how long it took."""
    trace = scorers.decision_trace(_record(inputs={"member_id": "12345"}))
    flat = " ".join(part for row in trace for part in row)
    assert "read_balance" in flat and "semantic_exact" in flat
    assert "ms" not in flat


# ---------------------------------------------------------------------- drift


def test_resolving_at_the_recorded_rung_is_not_drift() -> None:
    """Drift is descent *below what the artifact recorded*, not a weak rung winning."""
    steady = _record(
        inputs={"member_id": "12345"},
        strategy=ResolutionStrategy.STRUCTURAL_ANCHOR,
        recorded=ResolutionStrategy.STRUCTURAL_ANCHOR,
    )
    assert scorers.mean_drift([steady]) == 0.0
    assert scorers.fallback_rate([steady]) == 0.0


def test_falling_below_the_recorded_rung_is_drift() -> None:
    drifted = _record(
        inputs={"member_id": "12345"},
        strategy=ResolutionStrategy.STRUCTURAL_ANCHOR,
        recorded=ResolutionStrategy.SEMANTIC_EXACT,
    )
    assert scorers.mean_drift([drifted]) == 1.0
    assert scorers.fallback_rate([drifted]) == 1.0


def test_an_unauthorized_dispatch_is_counted_from_the_record() -> None:
    """Must always be zero, and read from the evidence rather than from intent."""
    bad = _record(inputs={"member_id": "12345"}, authorized=False)
    assert scorers.unauthorized_dispatches([bad]) == 1
    assert scorers.unauthorized_dispatches([_record(inputs={"member_id": "12345"})]) == 0
