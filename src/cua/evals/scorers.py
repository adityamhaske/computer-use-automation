"""Pure scoring functions over completed runs.

Every function here takes `RunRecord`s and returns numbers. No I/O, no browser, no clock -- so a
score can be recomputed from committed evidence without re-running anything, and a disagreement
between two reports is a difference in the runs rather than in the measurement.

The metrics are deliberately *not* collapsed into one number. Refusal rate and wrong-action rate
both describe "the run did not produce an answer", and averaging them would hide the only
distinction that matters: refusing is the system working, acting on the wrong control is an
incident.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cua.domain.result import FailureCode, RunStatus
from cua.domain.run_record import RunRecord


@dataclass(frozen=True)
class GroundTruth:
    """What a correct run must have returned, for one set of inputs.

    Wrong-action rate is only meaningful against a known-correct answer. The mock application is
    seeded deterministically precisely so this can exist -- against a real system it would have to
    come from a reconciliation source, which is the honest version of the same requirement.
    """

    inputs: dict[str, str]
    expect_status: RunStatus
    outputs: dict[str, str] = field(default_factory=dict)
    """Only the outputs worth pinning. `as_of` is the screen's own date and moves daily, so it is
    checked for presence by the capability's checkpoint rather than for value here."""

    outcome_code: str | None = None


def success_rate(records: list[RunRecord]) -> float:
    """Business outcomes count as successes.

    "No such member" is the capability working correctly and returning a negative answer. Scoring it
    as a failure would make the headline number worse every time the system did the right thing.
    """
    if not records:
        return 0.0
    good = sum(
        1
        for r in records
        if r.result is not None
        and r.result.status in {RunStatus.SUCCESS, RunStatus.BUSINESS_OUTCOME}
    )
    return good / len(records)


def wrong_action_count(records: list[RunRecord], truth: dict[str, GroundTruth]) -> int:
    """Runs that reported success but produced the wrong answer. **Target: zero, always.**

    A run is wrong when it claims SUCCESS and any pinned output differs from the seeded truth, or
    when it returns a business outcome that is not the one this member should produce. Reading
    another member's balance and reporting it confidently is the failure mode this whole system is
    shaped to prevent, so it is counted separately from every other kind of not-answering.

    A refusal, an escalation, or a hard failure is **not** a wrong action. The system declining to
    act is the designed behaviour.
    """
    wrong = 0
    for record in records:
        if record.result is None:
            continue
        key = _key(record.inputs)
        expected = truth.get(key)
        if expected is None:
            continue

        if record.result.status is RunStatus.SUCCESS:
            for name, value in expected.outputs.items():
                if record.result.outputs.get(name) != value:
                    wrong += 1
                    break
        elif record.result.status is RunStatus.BUSINESS_OUTCOME:
            observed = record.result.outcome.code if record.result.outcome else None
            if expected.outcome_code is not None and observed != expected.outcome_code:
                wrong += 1
    return wrong


def refusal_count(records: list[RunRecord]) -> int:
    """Runs that stopped rather than guess: an ambiguous target, or a state nothing declared.

    Reported next to the wrong-action rate and never traded off against it. A system tuned to refuse
    less will eventually act more often on a control it was not sure about.
    """
    refusals = 0
    for record in records:
        result = record.result
        if result is None or result.error is None:
            continue
        if result.error.code in {
            FailureCode.TARGET_AMBIGUOUS,
            FailureCode.UNEXPECTED_STATE,
            FailureCode.TARGET_NOT_FOUND,
        }:
            refusals += 1
    return refusals


def escalation_count(records: list[RunRecord]) -> int:
    return sum(
        1 for r in records if r.result is not None and r.result.status is RunStatus.NEEDS_HUMAN
    )


def decision_trace(record: RunRecord) -> tuple[tuple[str, ...], ...]:
    """The part of a run that must be identical between replays.

    Timings, run ids and wall-clock stamps are excluded because they are allowed to differ; what may
    not differ is *which control was chosen, by which rung of the ladder, and how the result was
    classified*. That is the claim "replay is deterministic" actually makes.
    """
    trace = []
    for step in record.steps:
        resolution = step.resolution
        trace.append(
            (
                step.step_id,
                step.action_type,
                str(step.actor),
                str(step.observation) if step.observation else "",
                str(resolution.strategy_used) if resolution and resolution.strategy_used else "",
                resolution.resolved_node_id or "" if resolution else "",
            )
        )
    return tuple(trace)


def determinism_holds(records: list[RunRecord]) -> bool:
    """True when every replay of the *same inputs* made exactly the same decisions.

    Grouped by inputs, which is the whole content of the claim. Comparing every run in a suite
    against one another would be comparing member 12345's run against member 99999's -- those are
    supposed to differ, and the first version of this function reported a healthy suite as
    non-deterministic for exactly that reason. Determinism means "same artifact, same inputs, same
    state, same decisions", not "every run looks alike".

    A boolean rather than a percentage on purpose: "94% deterministic" is not a property anyone can
    act on, and it invites tuning a number that should be a yes.
    """
    for group in _by_inputs(records).values():
        if len(group) < 2:
            continue
        first = decision_trace(group[0])
        if any(decision_trace(r) != first for r in group[1:]):
            return False
    return True


def _by_inputs(records: list[RunRecord]) -> dict[str, list[RunRecord]]:
    grouped: dict[str, list[RunRecord]] = {}
    for record in records:
        grouped.setdefault(_key(record.inputs), []).append(record)
    return grouped


def nondeterministic_cases(records: list[RunRecord]) -> list[str]:
    """Which input sets disagreed between replays. Empty when determinism holds."""
    offenders = []
    for key, group in _by_inputs(records).items():
        if len(group) < 2:
            continue
        first = decision_trace(group[0])
        if any(decision_trace(r) != first for r in group[1:]):
            offenders.append(key)
    return sorted(offenders)


def strategy_mix(records: list[RunRecord]) -> dict[str, int]:
    """Which rung of the ladder won, summed across runs."""
    mix: dict[str, int] = {}
    for record in records:
        for strategy, count in record.strategy_mix.items():
            mix[strategy] = mix.get(strategy, 0) + count
    return dict(sorted(mix.items()))


def mean_drift(records: list[RunRecord]) -> float:
    """Average share of resolutions that fell to a weaker rung than the artifact recorded.

    This is the early warning. It rises before replays start failing, which is the whole reason it
    is recorded per step rather than inferred from failures after the fact.
    """
    if not records:
        return 0.0
    return sum(r.drift_score for r in records) / len(records)


def fallback_rate(records: list[RunRecord]) -> float:
    """Share of resolutions that did not win on the strongest rung they were recorded at."""
    total = drifted = 0
    for record in records:
        for step in record.steps:
            if step.resolution is None or step.resolution.strategy_used is None:
                continue
            total += 1
            if step.resolution.drifted:
                drifted += 1
    return drifted / total if total else 0.0


def unauthorized_dispatches(records: list[RunRecord]) -> int:
    """Must be zero. Derived from the evidence on disk, not from what the executor believed."""
    return sum(len(r.unauthorized_dispatches) for r in records)


def _key(inputs: dict[str, object]) -> str:
    return ",".join(f"{k}={inputs[k]}" for k in sorted(inputs))


def truth_key(inputs: dict[str, str]) -> str:
    """The lookup key a `GroundTruth` is registered under."""
    return _key(dict(inputs))
