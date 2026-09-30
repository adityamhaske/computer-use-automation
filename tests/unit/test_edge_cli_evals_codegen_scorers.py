"""Edge cases for the eval scorers: the measuring equipment has to be right at the boundaries.

`tests/unit/test_evals.py` pins the headline claims (a wrong balance is a wrong action, a refusal
is not). This file is about the inputs around them: pinned values that differ only by a character a
human would not see, runs with nothing to score, inputs that collide once flattened into a lookup
key, and histograms over odd shapes of run.

A scorer that is wrong here produces a confident number nobody re-derives, which is worse than no
scorer at all -- so each case names the claim it defends.
"""

from __future__ import annotations

import pytest

from cua.domain.actor import Actor
from cua.domain.result import FailureCode, RunStatus
from cua.domain.run_record import RunRecord
from cua.domain.target import ResolutionStrategy
from cua.evals import scorers
from cua.evals.scorers import GroundTruth, truth_key
from tests.unit.test_edge_cli_evals_codegen_support import StepSpec, run_record

# Each marker names a real defect found by the test it decorates. strict=True: when the defect is
# fixed the test starts passing and the suite fails until the marker is deleted, so a fix cannot
# leave a stale "known failure" behind.


def _digits_in(block_zero: int, text: str) -> str:
    """`text` with its ASCII digits swapped for the same digits from another Unicode block."""
    return "".join(chr(block_zero + int(ch)) if ch.isdigit() else ch for ch in text)


# Built with chr() so the source stays ASCII; invisible characters in a test file are a hazard.
FULLWIDTH = _digits_in(0xFF10, "4210.55")
ARABIC_INDIC = _digits_in(0x0660, "4210.55")
ZWSP = chr(0x200B)
ACUTE = chr(0x0301)
RLO = chr(0x202E)

MEMBER = {"member_id": "12345"}
PINNED = {"savings_balance": "4210.55", "account_status": "Active"}

TRUTH = {
    truth_key(MEMBER): GroundTruth(inputs=MEMBER, expect_status=RunStatus.SUCCESS, outputs=PINNED),
    truth_key({"member_id": "99999"}): GroundTruth(
        inputs={"member_id": "99999"},
        expect_status=RunStatus.BUSINESS_OUTCOME,
        outcome_code="member_not_found",
    ),
}


def _success(outputs: dict[str, object], **kw: object) -> RunRecord:
    return run_record(inputs=MEMBER, outputs=outputs, **kw)  # type: ignore[arg-type]


# ----------------------------------------------- wrong-action rate against seeded truth


@pytest.mark.parametrize(
    ("outputs", "wrong"),
    [
        pytest.param(PINNED, 0, id="exactly-the-seeded-truth"),
        pytest.param({**PINNED, "as_of": "anything"}, 0, id="unpinned-extra-output-is-ignored"),
        pytest.param(
            {"savings_balance": "18730.00", "account_status": "Active"},
            1,
            id="another-members-balance",
        ),
        pytest.param({"savings_balance": "4210.55"}, 1, id="a-pinned-output-was-never-returned"),
        pytest.param({}, 1, id="success-with-no-outputs-at-all"),
        pytest.param({**PINNED, "savings_balance": "4210.55 "}, 1, id="trailing-space"),
        pytest.param({**PINNED, "savings_balance": " 4210.55"}, 1, id="leading-space"),
        pytest.param({**PINNED, "savings_balance": "4210.550"}, 1, id="extra-trailing-zero"),
        pytest.param({**PINNED, "savings_balance": "4,210.55"}, 1, id="thousands-grouping"),
        pytest.param({**PINNED, "account_status": "active"}, 1, id="case-differs"),
        # The comparison is exact on purpose: a balance that only *looks* identical is precisely
        # the silent wrong answer the metric exists to catch. None of these may be normalised away.
        pytest.param({**PINNED, "savings_balance": FULLWIDTH}, 1, id="fullwidth-digits"),
        pytest.param({**PINNED, "savings_balance": ARABIC_INDIC}, 1, id="arabic-indic-digits"),
        pytest.param({**PINNED, "savings_balance": "4210.55" + ZWSP}, 1, id="zero-width-space"),
        pytest.param({**PINNED, "account_status": "Active" + ACUTE}, 1, id="combining-accent"),
        pytest.param({**PINNED, "account_status": RLO + "evitcA"}, 1, id="right-to-left-override"),
        pytest.param({"savings_balance": "0.00", "account_status": "Closed"}, 1, id="both-wrong"),
    ],
)
def test_a_success_is_judged_against_the_pinned_outputs_exactly(
    outputs: dict[str, object], wrong: int
) -> None:
    assert scorers.wrong_action_count([_success(outputs)], TRUTH) == wrong


def test_a_run_with_two_wrong_fields_is_one_wrong_action() -> None:
    """The metric counts runs that acted wrongly, not wrong fields: one wrong click is one click."""
    both_wrong = _success({"savings_balance": "0.00", "account_status": "Closed"})
    assert scorers.wrong_action_count([both_wrong], TRUTH) == 1


def test_wrong_actions_are_counted_per_run_not_per_distinct_mistake() -> None:
    """Repeating the same wrong answer five times is five wrong actions."""
    records = [_success(PINNED) for _ in range(3)] + [
        _success({"savings_balance": "1.00", "account_status": "Active"}) for _ in range(2)
    ]
    assert scorers.wrong_action_count(records, TRUTH) == 2


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (RunStatus.FAILED, FailureCode.TARGET_AMBIGUOUS),
        (RunStatus.FAILED, FailureCode.UNEXPECTED_STATE),
        (RunStatus.FAILED, FailureCode.CHECKPOINT_FAILED),
        (RunStatus.FAILED, FailureCode.INTERNAL),
        (RunStatus.NEEDS_HUMAN, None),
    ],
)
def test_declining_to_answer_is_never_a_wrong_action(
    status: RunStatus, error: FailureCode | None
) -> None:
    """Refusal, escalation and hard failure all leave the wrong-action rate alone.

    Otherwise a system would be rewarded for acting on a control it was not sure about.
    """
    record = run_record(inputs=MEMBER, status=status, error=error)
    assert scorers.wrong_action_count([record], TRUTH) == 0


def test_the_correct_business_outcome_is_not_a_wrong_action() -> None:
    record = run_record(
        inputs={"member_id": "99999"},
        status=RunStatus.BUSINESS_OUTCOME,
        outcome="member_not_found",
    )
    assert scorers.wrong_action_count([record], TRUTH) == 0


def test_a_run_with_no_result_is_skipped_not_counted() -> None:
    assert scorers.wrong_action_count([run_record(inputs=MEMBER, with_result=False)], TRUTH) == 0


def test_inputs_with_no_seeded_truth_are_unjudged_not_wrong() -> None:
    """Wrong-action rate is only meaningful against a known-correct answer."""
    unseeded = run_record(inputs={"member_id": "00000"}, outputs={"savings_balance": "1.00"})
    assert scorers.wrong_action_count([unseeded], TRUTH) == 0


@pytest.mark.parametrize("records", [[], [None]], ids=["zero-runs", "one-run-without-a-result"])
def test_zero_scorable_runs_count_zero_wrong_actions(records: list[None]) -> None:
    runs = [run_record(inputs=MEMBER, with_result=False) for _ in records]
    assert scorers.wrong_action_count(runs, TRUTH) == 0
    assert scorers.wrong_action_count(runs, {}) == 0


def test_truth_lookup_ignores_the_order_inputs_were_supplied_in() -> None:
    truth = {
        truth_key({"a": "1", "b": "2"}): GroundTruth(
            inputs={"a": "1", "b": "2"}, expect_status=RunStatus.SUCCESS, outputs={"x": "ok"}
        )
    }
    reordered = run_record(inputs={"b": "2", "a": "1"}, outputs={"x": "WRONG"})
    assert scorers.wrong_action_count([reordered], truth) == 1, (
        "the record was not matched to its truth, so a wrong answer went uncounted"
    )


def test_a_business_outcome_where_the_seeded_truth_has_no_outcome_is_a_wrong_action() -> None:
    """Member 12345 has a balance. Answering "member_not_found" for them is confidently wrong.

    `wrong_action_count` documents a run as wrong "when it returns a business outcome that is not
    the one this member should produce" -- and a member who should SUCCEED should produce none.
    """
    says_no_such_member = run_record(
        inputs=MEMBER, status=RunStatus.BUSINESS_OUTCOME, outcome="member_not_found"
    )
    assert scorers.wrong_action_count([says_no_such_member], TRUTH) == 1


def test_a_success_where_the_seeded_truth_is_a_business_outcome_is_a_wrong_action() -> None:
    """Member 99999 does not exist. Returning a balance for them is the failure mode itself.

    `GroundTruth.expect_status` is "what a correct run must have returned"; a SUCCESS that carries
    outputs for a member the seed says is not found is not that.
    """
    invented_a_balance = run_record(
        inputs={"member_id": "99999"}, outputs={"savings_balance": "4210.55"}
    )
    assert scorers.wrong_action_count([invented_a_balance], TRUTH) == 1


# ----------------------------------------------------------------- key collisions


def test_the_truth_key_does_not_depend_on_insertion_order() -> None:
    assert truth_key({"b": "2", "a": "1"}) == truth_key({"a": "1", "b": "2"})


def test_the_truth_key_of_no_inputs_is_a_stable_value() -> None:
    assert truth_key({}) == truth_key({})
    assert isinstance(truth_key({}), str)


def test_inputs_that_differ_only_in_how_they_flatten_get_different_keys() -> None:
    """`a="1,b=2"` and `a="1", b="2"` are different calls; one key for both mixes their scores.

    Input values are arbitrary strings, so a value that contains the key's own separators can land
    on another call's key. The runs are then grouped together for determinism and scored against
    the other call's truth -- a silent wrong answer rather than an error.
    """
    assert truth_key({"a": "1,b=2"}) != truth_key({"a": "1", "b": "2"})


def test_unrelated_calls_with_colliding_keys_are_not_compared_for_determinism() -> None:
    decided_here = run_record(inputs={"a": "1,b=2"}, steps=[StepSpec(node_id="n1")])
    decided_there = run_record(inputs={"a": "1", "b": "2"}, steps=[StepSpec(node_id="n2")])
    assert scorers.nondeterministic_cases([decided_here, decided_there]) == []


# --------------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    "code",
    [FailureCode.TARGET_AMBIGUOUS, FailureCode.UNEXPECTED_STATE, FailureCode.TARGET_NOT_FOUND],
)
def test_a_refusal_code_is_counted_as_a_refusal(code: FailureCode) -> None:
    assert (
        scorers.refusal_count([run_record(inputs=MEMBER, status=RunStatus.FAILED, error=code)]) == 1
    )


@pytest.mark.parametrize(
    "code",
    [
        FailureCode.INTERNAL,
        FailureCode.TIMEOUT,
        FailureCode.ACTION_FAILED,
        FailureCode.INPUT_VALIDATION_FAILED,
        FailureCode.RECOVERY_EXHAUSTED,
    ],
)
def test_a_defect_or_a_bad_call_is_not_a_refusal(code: FailureCode) -> None:
    """A refusal is the system declining to guess. A timeout or a malformed call is not that."""
    assert (
        scorers.refusal_count([run_record(inputs=MEMBER, status=RunStatus.FAILED, error=code)]) == 0
    )


def test_refusals_ignore_runs_without_an_error_or_a_result() -> None:
    clean = run_record(inputs=MEMBER, outputs=PINNED)
    resultless = run_record(inputs=MEMBER, with_result=False)
    assert scorers.refusal_count([clean, resultless]) == 0
    assert scorers.refusal_count([]) == 0


def test_escalation_count_counts_only_needs_human() -> None:
    records = [
        run_record(inputs=MEMBER, status=RunStatus.NEEDS_HUMAN),
        run_record(inputs=MEMBER, status=RunStatus.FAILED, error=FailureCode.INTERNAL),
        run_record(inputs=MEMBER, outputs=PINNED),
        run_record(inputs=MEMBER, with_result=False),
    ]
    assert scorers.escalation_count(records) == 1
    assert scorers.escalation_count([]) == 0


# ------------------------------------------------------------------ success rate


def test_success_rate_of_no_runs_is_zero_not_a_division_error() -> None:
    assert scorers.success_rate([]) == 0.0


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([RunStatus.SUCCESS], 1.0),
        ([RunStatus.FAILED], 0.0),
        ([RunStatus.NEEDS_HUMAN], 0.0),
        (
            [
                RunStatus.SUCCESS,
                RunStatus.BUSINESS_OUTCOME,
                RunStatus.FAILED,
                RunStatus.NEEDS_HUMAN,
            ],
            0.5,
        ),
        ([RunStatus.SUCCESS] * 19 + [RunStatus.FAILED], 0.95),
    ],
)
def test_success_rate_counts_answers_including_negative_ones(
    statuses: list[RunStatus], expected: float
) -> None:
    records = [
        run_record(
            inputs=MEMBER,
            status=status,
            outcome="member_not_found" if status is RunStatus.BUSINESS_OUTCOME else None,
            error=FailureCode.INTERNAL if status is RunStatus.FAILED else None,
        )
        for status in statuses
    ]
    assert scorers.success_rate(records) == pytest.approx(expected)


# ------------------------------------------------------------------ determinism


def test_determinism_needs_the_same_inputs_to_be_replayed_more_than_once_to_fail() -> None:
    """One run of an input has nothing to disagree with."""
    lone = run_record(inputs=MEMBER)
    assert scorers.nondeterministic_cases([lone]) == []
    assert scorers.nondeterministic_cases([]) == []


def test_different_inputs_may_decide_differently_without_being_nondeterministic() -> None:
    a = run_record(inputs={"member_id": "1"}, steps=[StepSpec(node_id="row[1]")])
    b = run_record(inputs={"member_id": "2"}, steps=[StepSpec(node_id="row[9]")])
    # Each input repeats, so determinism is measured; singletons would report None (not measured).
    assert scorers.determinism_holds([a, a, b, b]) is True
    assert scorers.determinism_holds([a, b]) is None


def test_inputs_are_grouped_regardless_of_key_order() -> None:
    first = run_record(inputs={"a": "1", "b": "2"}, steps=[StepSpec(node_id="n1")])
    second = run_record(inputs={"b": "2", "a": "1"}, steps=[StepSpec(node_id="n2")])
    assert scorers.nondeterministic_cases([first, second]) == ["a=1,b=2"]


@pytest.mark.parametrize(
    "variant",
    [
        pytest.param(StepSpec(node_id="other"), id="different-node"),
        pytest.param(StepSpec(strategy=ResolutionStrategy.STRUCTURAL_ANCHOR), id="different-rung"),
        pytest.param(StepSpec(step_id="other_step"), id="different-step"),
        pytest.param(StepSpec(action_type="click"), id="different-action"),
        pytest.param(StepSpec(actor=Actor.HUMAN), id="different-actor"),
        pytest.param(StepSpec(node_id=None), id="node-disappeared"),
        pytest.param(StepSpec(strategy=None), id="rung-disappeared"),
        pytest.param(StepSpec(with_resolution=False), id="resolution-disappeared"),
    ],
)
def test_any_difference_in_the_decision_is_nondeterminism(variant: StepSpec) -> None:
    baseline = run_record(inputs=MEMBER, steps=[StepSpec()])
    changed = run_record(inputs=MEMBER, steps=[variant])
    assert scorers.determinism_holds([baseline, changed]) is False
    assert scorers.nondeterministic_cases([baseline, changed]) == ["member_id=12345"]


def test_an_extra_or_missing_step_is_nondeterminism() -> None:
    short = run_record(inputs=MEMBER, steps=[StepSpec(step_id="a")])
    long = run_record(inputs=MEMBER, steps=[StepSpec(step_id="a"), StepSpec(step_id="b")])
    assert scorers.determinism_holds([short, long]) is False
    assert scorers.determinism_holds([long, short]) is False


def test_step_order_matters() -> None:
    forward = run_record(inputs=MEMBER, steps=[StepSpec(step_id="a"), StepSpec(step_id="b")])
    reverse = run_record(inputs=MEMBER, steps=[StepSpec(step_id="b"), StepSpec(step_id="a")])
    assert scorers.determinism_holds([forward, reverse]) is False


def test_only_the_last_of_several_replays_disagreeing_is_still_caught() -> None:
    runs = [run_record(inputs=MEMBER, run_id=f"r{i}") for i in range(4)]
    runs.append(run_record(inputs=MEMBER, run_id="r4", steps=[StepSpec(node_id="drifted")]))
    assert scorers.nondeterministic_cases(runs) == ["member_id=12345"]


def test_offenders_are_reported_sorted_and_each_once() -> None:
    def pair(member: str) -> list[RunRecord]:
        return [
            run_record(inputs={"member_id": member}, steps=[StepSpec(node_id="x")]),
            run_record(inputs={"member_id": member}, steps=[StepSpec(node_id="y")]),
            run_record(inputs={"member_id": member}, steps=[StepSpec(node_id="z")]),
        ]

    offenders = scorers.nondeterministic_cases(pair("67890") + pair("12345"))
    assert offenders == ["member_id=12345", "member_id=67890"]


def test_run_ids_timings_and_evidence_paths_do_not_count_as_different_decisions() -> None:
    """Determinism is about which control was chosen, never about how long or under what id."""
    a = run_record(inputs=MEMBER, run_id="eval-00", duration_ms=10)
    b = run_record(inputs=MEMBER, run_id="eval-99", duration_ms=9_999)
    assert scorers.decision_trace(a) == scorers.decision_trace(b)
    assert scorers.determinism_holds([a, b]) is True


def test_the_decision_trace_tolerates_steps_that_resolved_nothing() -> None:
    """A step with no resolution (a reload, a navigation) has a trace row, with blanks."""
    record = run_record(
        inputs=MEMBER,
        steps=[StepSpec(step_id="reload", action_type="reload", with_resolution=False)],
    )
    row, final = scorers.decision_trace(record)
    assert row[0] == "reload" and row[1] == "reload"
    assert row[-2:] == ("", ""), "no rung and no node should be blank, not 'None'"
    assert final[0] == "<result>", "the final classification closes the trace"


def test_the_decision_trace_is_comparable_and_hashable() -> None:
    trace = scorers.decision_trace(run_record(inputs=MEMBER))
    assert isinstance(trace, tuple)
    assert hash(trace) == hash(scorers.decision_trace(run_record(inputs=MEMBER)))


def test_a_run_ending_differently_with_the_same_steps_is_nondeterminism() -> None:
    """`decision_trace` promises "how the result was classified" must not differ between replays.

    One replay reaches the goal; the other takes the very same steps and then fails its checkpoint.
    Every step row is identical, so a trace built from step rows alone reports a healthy suite.
    """
    same_steps = [StepSpec(step_id="a"), StepSpec(step_id="b")]
    reached = run_record(inputs=MEMBER, outputs=PINNED, steps=same_steps)
    missed = run_record(
        inputs=MEMBER,
        status=RunStatus.FAILED,
        error=FailureCode.CHECKPOINT_FAILED,
        steps=same_steps,
    )
    assert scorers.determinism_holds([reached, missed]) is False


# -------------------------------------------------------------- strategy histogram


def test_the_strategy_histogram_of_no_runs_is_empty() -> None:
    assert scorers.strategy_mix([]) == {}


def test_the_strategy_histogram_sums_across_runs_and_sorts_its_keys() -> None:
    a = run_record(
        inputs=MEMBER,
        steps=[
            StepSpec(step_id="1", strategy=ResolutionStrategy.STRUCTURAL_ANCHOR),
            StepSpec(step_id="2", strategy=ResolutionStrategy.SEMANTIC_EXACT),
        ],
    )
    b = run_record(
        inputs=MEMBER,
        steps=[StepSpec(step_id="1", strategy=ResolutionStrategy.STRUCTURAL_ANCHOR)],
    )
    mix = scorers.strategy_mix([a, b])
    assert mix == {"semantic_exact": 1, "structural_anchor": 2}
    assert list(mix) == sorted(mix)


def test_steps_with_no_resolution_or_no_rung_do_not_enter_the_histogram() -> None:
    record = run_record(
        inputs=MEMBER,
        steps=[
            StepSpec(step_id="1", with_resolution=False),
            StepSpec(step_id="2", strategy=None),
            StepSpec(step_id="3"),
        ],
    )
    assert scorers.strategy_mix([record]) == {"semantic_exact": 1}


def test_the_strategy_histogram_is_a_fresh_dict_each_call() -> None:
    records = [run_record(inputs=MEMBER)]
    first = scorers.strategy_mix(records)
    first["semantic_exact"] = 999
    assert scorers.strategy_mix(records) == {"semantic_exact": 1}


# ---------------------------------------------------------------------- drift


def test_drift_and_fallback_rate_of_no_runs_are_zero() -> None:
    assert scorers.mean_drift([]) == 0.0
    assert scorers.fallback_rate([]) == 0.0


def test_resolving_on_a_stronger_rung_than_recorded_is_not_drift() -> None:
    """Drift is descent. A step recorded at a weak rung that now wins a strong one has improved."""
    improved = run_record(
        inputs=MEMBER,
        steps=[
            StepSpec(
                strategy=ResolutionStrategy.SEMANTIC_EXACT,
                recorded=ResolutionStrategy.STRUCTURAL_ANCHOR,
            )
        ],
    )
    assert scorers.mean_drift([improved]) == 0.0
    assert scorers.fallback_rate([improved]) == 0.0


def test_a_step_with_no_recorded_rung_cannot_drift() -> None:
    unrecorded = run_record(inputs=MEMBER, steps=[StepSpec(recorded=None)])
    assert scorers.mean_drift([unrecorded]) == 0.0


def test_mean_drift_averages_runs_while_fallback_rate_averages_resolutions() -> None:
    """They are different denominators: one drifted step in a one-step run vs a three-step run.

    Run A: one resolution, drifted   -> drift 1.0
    Run B: three resolutions, steady -> drift 0.0
    Per run the mean is 0.5; per resolution one in four fell back.
    """
    drifted = StepSpec(
        strategy=ResolutionStrategy.STRUCTURAL_ANCHOR,
        recorded=ResolutionStrategy.SEMANTIC_EXACT,
    )
    a = run_record(inputs=MEMBER, steps=[drifted])
    b = run_record(inputs=MEMBER, steps=[StepSpec(step_id=str(i)) for i in range(3)])
    assert scorers.mean_drift([a, b]) == pytest.approx(0.5)
    assert scorers.fallback_rate([a, b]) == pytest.approx(0.25)


def test_fallback_rate_skips_steps_that_resolved_nothing() -> None:
    record = run_record(
        inputs=MEMBER,
        steps=[StepSpec(with_resolution=False), StepSpec(strategy=None), StepSpec()],
    )
    assert scorers.fallback_rate([record]) == 0.0


# --------------------------------------------------------------- unauthorized


def test_unauthorized_dispatches_count_every_offending_step() -> None:
    bad = run_record(
        inputs=MEMBER,
        steps=[
            StepSpec(step_id="a", authorized=False),
            StepSpec(step_id="b"),
            StepSpec(step_id="c", authorized=False),
        ],
    )
    assert scorers.unauthorized_dispatches([bad, run_record(inputs=MEMBER)]) == 2
    assert scorers.unauthorized_dispatches([]) == 0


# ----------------------------------------------------------------- ground truth


def test_ground_truth_outputs_default_is_not_shared_between_instances() -> None:
    first = GroundTruth(inputs={"a": "1"}, expect_status=RunStatus.SUCCESS)
    second = GroundTruth(inputs={"a": "2"}, expect_status=RunStatus.SUCCESS)
    assert first.outputs == {} and first.outputs is not second.outputs
