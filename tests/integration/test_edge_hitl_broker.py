"""`SessionBroker` edge cases: illegal call orders, refused calls leaving no trace, the walked-away
operator, and what the evidence says about all of it.

Offline: the broker, dispatcher, policy engine and evidence bus are the production ones, over a
recording fake surface (`test_edge_hitl_rig.py`). `test_handoff.py` already proves the happy path on
a real browser; everything here is what that file does not reach.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import timedelta
from pathlib import Path

import pytest

from cua.domain.action import RawInput, RawInputKind
from cua.domain.actor import Actor
from cua.evidence.bus import EventType
from cua.hitl.intervention import InterventionState
from cua.hitl.lease import ControlState, LeaseError
from cua.runtime.dispatcher import DispatchStatus
from tests.integration.test_edge_hitl_rig import (
    ACCOUNT,
    EMAIL,
    SSN,
    Rig,
    build_rig,
    escalate,
    events,
    page,
)

CLICK = RawInput(kind=RawInputKind.MOUSE_CLICK, x=10, y=10)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return build_rig(tmp_path)


def _claimed(rig: Rig, operator: str = "alex", **kwargs: object):
    request = escalate(rig)
    rig.broker.claim(request.intervention_id, operator, **kwargs)
    return request


def _all_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".json", ".jsonl"}
    )


# ------------------------------------------------------------------------------- escalate


@pytest.mark.parametrize("phase", ["paused", "human_control", "resuming"])
def test_a_second_escalation_is_refused_and_opens_nothing(rig: Rig, phase: str) -> None:
    """One stuck session is one intervention. A refused escalation must not leave an orphan request
    in the queue, nor an ESCALATE event claiming the run stopped a second time."""
    first = escalate(rig)
    if phase in ("human_control", "resuming"):
        rig.broker.claim(first.intervention_id, "alex")
    if phase == "resuming":
        rig.broker.release(snapshot_after=page())

    epoch_before = rig.broker.lease.epoch
    with pytest.raises(LeaseError, match="cannot escalate"):
        escalate(rig, run_id="run-2")

    assert rig.broker.lease.epoch == epoch_before
    assert list(rig.broker.queue.requests) == [first.intervention_id]
    assert len(events(rig, EventType.ESCALATE.value)) == 1


def test_every_escalation_gets_its_own_id_and_points_at_its_evidence(rig: Rig) -> None:
    ids = []
    for _ in range(12):
        request = escalate(rig)
        ids.append(request.intervention_id)
        assert request.evidence_ref == str(rig.bus.run_dir)
        assert request.state is InterventionState.OPEN
        rig.broker.claim(request.intervention_id, "alex")
        rig.broker.release(snapshot_after=rig.driver.snapshot)
        rig.broker.resume()

    assert len(set(ids)) == 12, "an id that repeats would make one handoff overwrite another"
    assert all(i.startswith("int-") for i in ids)


@pytest.mark.parametrize(
    "overrides",
    [
        {"reason": ""},
        {"reason": "   "},
        {"goal": ""},
        {"step_id": None, "failure_code": None},
        {"snapshot": None},
        {"reason": "e\N{COMBINING ACUTE ACCENT} \N{ZERO WIDTH SPACE} \N{RIGHT-TO-LEFT OVERRIDE}x"},
        {"reason": "{0} {reason} %s ${jndi:ldap://x} <script>alert(1)</script>"},
        {"reason": "x" * 5_000},
    ],
    ids=[
        "empty-reason",
        "whitespace-reason",
        "empty-goal",
        "no-step-no-code",
        "no-snapshot",
        "unicode",
        "template-and-html",
        "very-long",
    ],
)
def test_degenerate_escalation_text_does_not_crash_the_path(rig: Rig, overrides: dict) -> None:
    """The broker must still pause the session and leave a parseable trace for any text at all --
    escalation is the path that runs when something is already wrong, so it cannot itself be the
    thing that raises."""
    request = escalate(rig, **overrides)

    assert rig.broker.lease.state is ControlState.PAUSED
    assert request.state is InterventionState.OPEN
    # Every trace line must still be valid JSON (read_events parses each one).
    assert events(rig, EventType.ESCALATE.value)


# ---------------------------------------------------------------------------------- claim


def test_claiming_an_unknown_intervention_changes_nothing(rig: Rig) -> None:
    escalate(rig)
    epoch = rig.broker.lease.epoch

    with pytest.raises(KeyError):
        rig.broker.claim("int-does-not-exist", "alex")

    assert rig.broker.lease.state is ControlState.PAUSED
    assert rig.broker.lease.epoch == epoch
    assert rig.broker.handoff is None
    assert events(rig, EventType.LEASE.value) == []


def test_the_second_claimant_loses_and_leaves_the_first_undisturbed(rig: Rig) -> None:
    request = _claimed(rig, "alex")
    epoch = rig.broker.lease.epoch

    with pytest.raises(ValueError, match="claimed"):
        rig.broker.claim(request.intervention_id, "sam")

    assert rig.broker.lease.operator == "alex"
    assert rig.broker.lease.epoch == epoch, "a lost race must not advance the epoch"
    assert rig.broker.handoff is not None and rig.broker.handoff.operator == "alex"
    assert rig.broker.queue.get(request.intervention_id).operator == "alex"
    assert len([e for e in events(rig, EventType.LEASE.value) if e["transition"] == "claimed"]) == 1


def test_an_intervention_that_was_already_resolved_cannot_be_claimed_again(rig: Rig) -> None:
    """A replayed or stale claim for finished work is refused, and does not pause a running
    session."""
    request = _claimed(rig)
    rig.broker.release(snapshot_after=rig.driver.snapshot)
    rig.broker.resume()
    assert rig.broker.lease.state is ControlState.RUNNING

    with pytest.raises(ValueError, match="resolved"):
        rig.broker.claim(request.intervention_id, "sam")

    assert rig.broker.lease.state is ControlState.RUNNING
    assert rig.broker.lease.operator is None


def test_a_claim_that_fails_leaves_the_session_claimable(rig: Rig) -> None:
    """A refused call must be side-effect free -- the same standard the lease holds itself to.

    `timedelta.max` is type-legal and overflows `now + hold_for`. `Lease.claim` has already set
    `operator`, and `SessionBroker.claim` has already moved the request to CLAIMED, so the failure
    strands the intervention (invisible to `pending()`) on a session that is still PAUSED.
    """
    request = escalate(rig)
    epoch = rig.broker.lease.epoch

    with pytest.raises(Exception):  # noqa: B017 - whichever typed error the fix chooses
        rig.broker.claim(request.intervention_id, "alex", hold_for=timedelta.max)

    assert rig.broker.lease.state is ControlState.PAUSED
    assert rig.broker.lease.epoch == epoch
    assert rig.broker.lease.operator is None, "a PAUSED session must not name an operator"
    assert rig.broker.queue.get(request.intervention_id).state is InterventionState.OPEN


def test_a_hold_of_zero_is_reclaimed_by_the_first_sweep(rig: Rig) -> None:
    """The boundary of the deadline: zero is already over, so the very next sweep reclaims it."""
    request = _claimed(rig, "alex", hold_for=timedelta(0))

    assert rig.broker.sweep() is True
    assert rig.broker.lease.state is ControlState.PAUSED
    assert rig.broker.queue.get(request.intervention_id).state is InterventionState.ABANDONED


# ------------------------------------------------------------------------- human_action


@pytest.mark.parametrize(
    "phase", ["running", "paused", "resuming", "resumed", "swept", "lapsed-unswept"]
)
def test_a_human_action_needs_a_live_human_hold(rig: Rig, phase: str) -> None:
    """Six ways a session can be in nobody's human hands, and in none of them does an operator
    gesture reach the surface -- nor leave an authorization behind suggesting it might have."""
    if phase != "running":
        request = escalate(rig)
        if phase != "paused":
            hold = (
                timedelta(seconds=-1)
                if phase in ("swept", "lapsed-unswept")
                else timedelta(hours=1)
            )
            rig.broker.claim(request.intervention_id, "alex", hold_for=hold)
        if phase == "swept":
            assert rig.broker.sweep() is True
        if phase in ("resuming", "resumed"):
            rig.broker.release(snapshot_after=page())
        if phase == "resumed":
            rig.broker.resume()

    with pytest.raises(LeaseError):
        rig.broker.human_action(CLICK)

    assert rig.driver.dispatched == []
    assert events(rig, EventType.AUTHORIZE.value) == []
    assert events(rig, EventType.DISPATCH.value) == []


def test_a_lapsed_hold_says_so_when_the_operator_acts(rig: Rig) -> None:
    _claimed(rig, hold_for=timedelta(seconds=-1))

    with pytest.raises(LeaseError, match="expired"):
        rig.broker.human_action(CLICK)


def test_raw_input_without_a_handoff_is_refused_even_if_the_lease_says_human(rig: Rig) -> None:
    """Belt and braces: the broker's own guard, reached by claiming the lease behind its back."""
    escalate(rig)
    rig.broker.lease.claim("alex")  # deliberately bypassing broker.claim, so no handoff exists
    assert rig.broker.handoff is None

    with pytest.raises(LeaseError, match="active handoff"):
        rig.broker.human_action(CLICK)

    assert rig.driver.dispatched == []


def test_each_operator_action_is_counted_once_in_the_handoff(rig: Rig) -> None:
    request = _claimed(rig)
    for _ in range(3):
        assert rig.broker.human_action(CLICK).status is DispatchStatus.OK

    assert rig.broker.handoff is not None and rig.broker.handoff.actions == 3
    rig.broker.release(snapshot_after=rig.driver.snapshot)

    released = next(e for e in events(rig, EventType.LEASE.value) if e["transition"] == "released")
    assert released["human_actions"] == 3
    assert rig.broker.queue.get(request.intervention_id).human_actions == 3


def test_a_new_claim_starts_a_fresh_handoff_count(rig: Rig) -> None:
    """The count belongs to one handoff. Carrying it into the next would credit an operator with the
    previous operator's work."""
    first = _claimed(rig, "alex")
    rig.broker.human_action(CLICK)
    rig.broker.human_action(CLICK)
    rig.broker.release(snapshot_after=rig.driver.snapshot)
    rig.broker.resume()

    second = escalate(rig, run_id="run-2")
    rig.broker.claim(second.intervention_id, "sam")
    rig.broker.human_action(CLICK)

    assert rig.broker.handoff is not None
    assert rig.broker.handoff.operator == "sam" and rig.broker.handoff.actions == 1
    assert rig.broker.queue.get(first.intervention_id).human_actions == 2


# --------------------------------------------------------------------------------- release


def test_releasing_with_no_handoff_is_refused(rig: Rig) -> None:
    with pytest.raises(LeaseError, match="no handoff"):
        rig.broker.release(snapshot_after=page())

    assert rig.broker.lease.state is ControlState.RUNNING
    assert events(rig, EventType.LEASE.value) == []


@pytest.mark.parametrize(
    ("before", "after"),
    [(True, False), (False, True), (False, False)],
    ids=["no-after-snapshot", "no-before-snapshot", "neither"],
)
def test_a_handoff_without_both_snapshots_is_reported_as_not_captured(
    rig: Rig, before: bool, after: bool
) -> None:
    """Never a fabricated "nothing changed": if the two halves of the diff are not both there, the
    record has to say it could not tell."""
    request = escalate(rig, snapshot=page() if before else None)
    rig.broker.claim(request.intervention_id, "alex")

    delta = rig.broker.release(snapshot_after=page() if after else None)

    assert delta is None
    assert rig.broker.queue.get(request.intervention_id).human_delta == "not captured"
    released = next(e for e in events(rig, EventType.LEASE.value) if e["transition"] == "released")
    assert released["human_delta"] is None


def test_an_unchanged_surface_is_reported_as_unchanged(rig: Rig) -> None:
    request = escalate(rig, snapshot=page())
    rig.broker.claim(request.intervention_id, "alex")

    delta = rig.broker.release(snapshot_after=page())

    assert delta is not None and not delta.changed
    assert "changed nothing" in rig.broker.queue.get(request.intervention_id).human_delta


def test_the_evidence_names_an_edited_field_but_never_carries_what_was_typed(rig: Rig) -> None:
    """Evidence records *that* a field was edited and which one. The value is regulated data: it
    does not belong in a trace just because a person typed it."""
    request = escalate(rig, snapshot=page(value="old-value"))
    rig.broker.claim(request.intervention_id, "alex")

    delta = rig.broker.release(snapshot_after=page(value=SSN))

    assert delta is not None and delta.values_changed == (("n1", "old-value", SSN),)
    released = next(e for e in events(rig, EventType.LEASE.value) if e["transition"] == "released")
    assert released["values_changed"] == ["n1"]
    assert SSN not in _all_text(rig.bus.run_dir)
    assert "old-value" not in _all_text(rig.bus.run_dir)


def test_a_refused_second_release_does_not_rewrite_what_the_operator_did(rig: Rig) -> None:
    """A replayed hand-back (a double click, a retry) is refused by the lease -- and must leave the
    record of the first one alone. The handoff is the one document that says what changed while a
    person held a regulated session."""
    request = escalate(rig, snapshot=page(value="a"))
    rig.broker.claim(request.intervention_id, "alex")
    first = rig.broker.release(snapshot_after=page(value="b"))
    assert rig.broker.handoff is not None
    recorded = (rig.broker.handoff.delta, rig.broker.handoff.snapshot_after)

    with pytest.raises(LeaseError):
        rig.broker.release(snapshot_after=page(value="c"))

    assert first is not None
    assert (rig.broker.handoff.delta, rig.broker.handoff.snapshot_after) == recorded


# ---------------------------------------------------------------------------------- resume


def test_resume_needs_a_release_first(rig: Rig) -> None:
    request = _claimed(rig)
    epoch = rig.broker.lease.epoch

    with pytest.raises(LeaseError, match="cannot resume"):
        rig.broker.resume()

    assert rig.broker.lease.epoch == epoch
    assert rig.broker.queue.get(request.intervention_id).state is InterventionState.CLAIMED
    assert [e["transition"] for e in events(rig, EventType.LEASE.value)] == ["claimed"]


def test_resume_is_not_repeatable(rig: Rig) -> None:
    request = _claimed(rig)
    rig.broker.release(snapshot_after=rig.driver.snapshot)
    epoch = rig.broker.resume()

    with pytest.raises(LeaseError):
        rig.broker.resume()

    assert rig.broker.lease.epoch == epoch
    assert rig.broker.queue.get(request.intervention_id).state is InterventionState.RESOLVED
    resumed = [e for e in events(rig, EventType.LEASE.value) if e["transition"] == "resumed"]
    assert len(resumed) == 1


def test_a_whole_cycle_leaves_an_ordered_actor_tagged_trail(rig: Rig) -> None:
    """The evidence is the proof of who held the session when: four lease events, each at the epoch
    the transition produced, each tagged with the party that made it."""
    request = escalate(rig)
    rig.broker.claim(request.intervention_id, "alex")
    rig.broker.human_action(CLICK)
    rig.broker.release(snapshot_after=rig.driver.snapshot)
    rig.broker.resume()

    trail = [
        (e["event"], e.get("transition"), e["actor"], e["lease_epoch"])
        for e in rig.bus.read_events()
        if e["event"] in (EventType.ESCALATE.value, EventType.LEASE.value)
    ]
    assert trail == [
        ("escalate", None, "system", 2),
        ("lease", "claimed", "human", 3),
        ("lease", "released", "human", 4),
        ("lease", "resumed", "automation", 5),
    ]
    assert rig.bus.unauthorized_dispatches() == []


# ----------------------------------------------------------------------------------- sweep


@pytest.mark.parametrize("phase", ["running", "paused", "human_control", "resuming"])
def test_sweeping_a_session_nobody_let_lapse_does_nothing(rig: Rig, phase: str) -> None:
    if phase != "running":
        request = escalate(rig)
        if phase in ("human_control", "resuming"):
            rig.broker.claim(request.intervention_id, "alex")
        if phase == "resuming":
            rig.broker.release(snapshot_after=page())
    before = (rig.broker.lease.state, rig.broker.lease.epoch, len(rig.bus.read_events()))

    assert rig.broker.sweep() is False

    assert (rig.broker.lease.state, rig.broker.lease.epoch, len(rig.bus.read_events())) == before


def test_a_sweep_reclaims_once_and_repeated_polls_do_not_advance_the_epoch(rig: Rig) -> None:
    """The console sweeps on every poll. If each poll moved the epoch, a session would never settle
    and an automation run holding the epoch it was given would be invalidated by merely watching."""
    request = _claimed(rig, "alex", hold_for=timedelta(seconds=-1))
    claimed_epoch = rig.broker.lease.epoch

    assert rig.broker.sweep() is True
    settled = rig.broker.lease.epoch
    assert settled == claimed_epoch + 1

    for _ in range(5):
        assert rig.broker.sweep() is False
    assert rig.broker.lease.epoch == settled

    expired = [e for e in events(rig, EventType.LEASE.value) if e["transition"] == "hold expired"]
    assert len(expired) == 1
    assert expired[0]["actor"] == "system" and expired[0]["operator"] == "alex"
    abandoned = rig.broker.queue.get(request.intervention_id)
    assert abandoned.state is InterventionState.ABANDONED
    assert "alex" in abandoned.resolution_note and "expired" in abandoned.resolution_note
    assert rig.broker.handoff is None


def test_an_operator_who_returns_after_the_sweep_cannot_hand_back_or_act(rig: Rig) -> None:
    """They walked away; the system took the session back. Coming back must not let them release
    (and so resume automation on) a hold that is no longer theirs."""
    _claimed(rig, "alex", hold_for=timedelta(seconds=-1))
    rig.broker.sweep()
    epoch = rig.broker.lease.epoch

    with pytest.raises(LeaseError):
        rig.broker.release(snapshot_after=page())
    with pytest.raises(LeaseError):
        rig.broker.human_action(CLICK)

    assert rig.broker.lease.state is ControlState.PAUSED and rig.broker.lease.epoch == epoch
    assert rig.driver.dispatched == []


def test_a_session_whose_operator_walked_away_can_be_taken_by_someone_else(rig: Rig) -> None:
    """`sweep()` promises the session "returns to the queue": that "the next operator needs to know
    a person had this and stopped". But an ABANDONED request is neither listed by `pending()` nor
    claimable, and `escalate()` refuses a PAUSED lease -- so after the sweep nothing in the system
    can move the session again, and automation stays locked out for good."""
    request = _claimed(rig, "alex", hold_for=timedelta(seconds=-1))
    rig.broker.sweep()
    assert rig.broker.lease.state is ControlState.PAUSED

    assert request.intervention_id in [r.intervention_id for r in rig.broker.queue.pending()], (
        "the abandoned intervention must be offered to the next operator"
    )
    epoch = rig.broker.claim(request.intervention_id, "sam")

    assert rig.broker.lease.operator == "sam" and epoch == rig.broker.lease.epoch


# --------------------------------------------------------------------------- redaction


def test_lease_events_redact_what_an_operator_name_or_reason_carries(rig: Rig) -> None:
    """A free-text field reaching the evidence bus is redacted on the way in, however it got
    there: the operator name comes from a URL query string and the reason from a page."""
    request = escalate(rig, reason=f"saw {EMAIL} and account {ACCOUNT}")
    rig.broker.claim(request.intervention_id, f"agent {SSN}")
    rig.broker.release(snapshot_after=rig.driver.snapshot)

    written = _all_text(rig.bus.run_dir)
    for secret in (SSN, EMAIL, ACCOUNT):
        assert secret not in written, f"{secret} reached the evidence"
    assert "<redacted:ssn>" in written


@pytest.mark.parametrize(
    "where",
    ["reason", "human_delta"],
)
def test_the_operators_queue_is_redacted_like_every_other_sink(rig: Rig, where: str) -> None:
    """`intervention.py`: "an intervention queue is a sink like any other -- arguably the most
    exposed one, since it is the artifact a human actually reads." AGENTS.md invariant 6 says every
    sink. The evidence bus masks the same strings, so the trace is clean while the card is not."""
    leak = f"expected the row for {EMAIL}, saw SSN {SSN} on account {ACCOUNT}"
    if where == "reason":
        request = escalate(rig, reason=leak)
        shown = " ".join(str(v) for v in request.context_card().values())
    else:
        request = escalate(rig, snapshot=page(url=f"http://localhost:8811/m?ssn={SSN}"))
        rig.broker.claim(request.intervention_id, "alex")
        rig.broker.release(snapshot_after=page(url=f"http://localhost:8811/n?ssn={SSN}"))
        shown = rig.broker.queue.get(request.intervention_id).human_delta

    for secret in (SSN, EMAIL, ACCOUNT):
        assert secret not in shown, f"{secret} is in the text the operator is shown"


# --------------------------------------------------------------------------- partial cycles


def test_three_full_cycles_in_a_row_never_reuse_or_reopen_anything(rig: Rig) -> None:
    epochs = []
    for n in range(3):
        request = escalate(rig, run_id=f"run-{n}")
        rig.broker.claim(request.intervention_id, f"op-{n}")
        assert rig.broker.human_action(CLICK).status is DispatchStatus.OK
        rig.broker.release(snapshot_after=rig.driver.snapshot)
        epochs.append(rig.broker.resume())

    assert epochs == [5, 9, 13], "four transitions per cycle, strictly increasing"
    states = {r.state for r in rig.broker.queue.requests.values()}
    assert states == {InterventionState.RESOLVED}
    assert rig.broker.queue.pending() == []
    assert rig.broker.lease.holder is Actor.AUTOMATION


# ------------------------------------------------------------------ resource budgets


def _redact_in_a_child_process(size: int, *, timeout: float) -> subprocess.CompletedProcess[str]:
    """Redact one `size`-character token in a separate process with a hard timeout.

    A separate process because a hung regex cannot be interrupted in-thread and must not be left
    spinning behind the rest of the suite.
    """
    repo = Path(__file__).resolve().parents[2]
    program = textwrap.dedent(
        f"""
        from pathlib import Path
        from cua.policy.config import parse_policy
        from cua.policy.redact import Redactor

        config = parse_policy(Path("config/policy.yaml").read_text())
        print(len(Redactor(config.redaction).text("x" * {size})))
        """
    )
    return subprocess.run(
        [sys.executable, "-c", program],
        cwd=repo,
        env={"PYTHONPATH": str(repo / "src"), "PATH": ""},
        timeout=timeout,
        check=True,
        capture_output=True,
        text=True,
    )


def test_the_redaction_probe_itself_runs() -> None:
    """Guards the xfail below: it must be failing because redaction is slow, not because the child
    process could not start."""
    done = _redact_in_a_child_process(1_000, timeout=30)
    assert done.stdout.strip() == "1000", done.stderr


@pytest.mark.slow
def test_redacting_one_huge_token_finishes_within_a_bounded_time() -> None:
    """Escalation text is page-derived, and pages carry long unbroken tokens (base64 blobs, data
    URIs, minified scripts). `EvidenceBus.emit` runs every such string through `Redactor.text`.

    The margin is enormous in both directions -- a linear redactor takes milliseconds here, the
    current rule minutes -- so this asserts a budget, not a stopwatch reading.
    """
    done = _redact_in_a_child_process(400_000, timeout=4)
    assert done.stdout.strip() == "400000"
