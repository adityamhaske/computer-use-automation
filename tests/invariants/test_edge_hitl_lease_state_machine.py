"""The lease is a state machine, and "who may act right now" must stay a checkable fact.

`test_handoff.py` proves the happy cycle and the stale-epoch race against a real browser. This file
covers what a happy path cannot: every *illegal* transition, that a refused transition leaves no
trace, that the epoch only ever moves forward, and that no sequence of calls -- however perverse --
reaches a state the design does not describe (a human holding a session with no deadline, an
operator named on a session nobody holds, a holder that disagrees with the state).

Pure, in-process and clock-free: a hold is made to lapse by giving it a deadline in the past rather
than by waiting for one.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import timedelta

import pytest

from cua.domain.actor import Actor
from cua.hitl.lease import ControlState, Lease, LeaseError

PAST = timedelta(seconds=-1)
FUTURE = timedelta(hours=1)

# ------------------------------------------------------------------ reaching each state


def _running() -> Lease:
    return Lease(session_id="sess-1")


def _paused() -> Lease:
    lease = _running()
    lease.escalate("stuck")
    return lease


def _human() -> Lease:
    lease = _paused()
    lease.claim("alex", hold_for=FUTURE)
    return lease


def _human_lapsed() -> Lease:
    lease = _paused()
    lease.claim("alex", hold_for=PAST)
    return lease


def _resuming() -> Lease:
    lease = _human()
    lease.release()
    return lease


STATES: dict[str, Callable[[], Lease]] = {
    "running": _running,
    "paused": _paused,
    "human_control": _human,
    "human_control_lapsed": _human_lapsed,
    "resuming": _resuming,
}

TRANSITIONS: dict[str, Callable[[Lease], int]] = {
    "escalate": lambda lease: lease.escalate("reason"),
    "claim": lambda lease: lease.claim("sam"),
    "release": lambda lease: lease.release(),
    "resume": lambda lease: lease.resume(),
    "reclaim_expired": lambda lease: lease.reclaim_expired(),
}

# The design's state machine (docs/design/control-transfer.md), written out independently of the
# implementation so the tests below compare the code against the spec, not against itself.
LEGAL: dict[str, set[str]] = {
    "running": {"escalate"},
    "paused": {"claim"},
    "human_control": {"release"},
    # A lapsed hold may still be handed back by the operator, or reclaimed by the system.
    "human_control_lapsed": {"release", "reclaim_expired"},
    "resuming": {"resume"},
}

AFTER: dict[tuple[str, str], tuple[ControlState, Actor]] = {
    ("running", "escalate"): (ControlState.PAUSED, Actor.SYSTEM),
    ("paused", "claim"): (ControlState.HUMAN_CONTROL, Actor.HUMAN),
    ("human_control", "release"): (ControlState.RESUMING, Actor.SYSTEM),
    ("human_control_lapsed", "release"): (ControlState.RESUMING, Actor.SYSTEM),
    ("human_control_lapsed", "reclaim_expired"): (ControlState.PAUSED, Actor.SYSTEM),
    ("resuming", "resume"): (ControlState.RUNNING, Actor.AUTOMATION),
}

HOLDER_FOR_STATE: dict[ControlState, Actor] = {
    ControlState.RUNNING: Actor.AUTOMATION,
    ControlState.PAUSED: Actor.SYSTEM,
    ControlState.HUMAN_CONTROL: Actor.HUMAN,
    ControlState.RESUMING: Actor.SYSTEM,
}


def _fingerprint(lease: Lease) -> tuple:
    return (
        lease.state,
        lease.holder,
        lease.epoch,
        lease.operator,
        lease.expires_at,
        tuple(lease.history),
    )


# ------------------------------------------------------------- every transition, every state


@pytest.mark.parametrize("transition", sorted(TRANSITIONS))
@pytest.mark.parametrize("state", sorted(STATES))
def test_a_transition_is_legal_exactly_where_the_design_says(state: str, transition: str) -> None:
    """25 (state, transition) pairs, each either permitted or refused with a `LeaseError`.

    Refusal has to be a typed error: a transition that silently no-ops would let a caller believe it
    holds a session it does not.
    """
    lease = STATES[state]()
    before_epoch = lease.epoch

    if transition in LEGAL[state]:
        new_epoch = TRANSITIONS[transition](lease)
        assert new_epoch == before_epoch + 1, "every transition advances the epoch by exactly one"
        assert (lease.state, lease.holder) == AFTER[(state, transition)]
    else:
        with pytest.raises(LeaseError):
            TRANSITIONS[transition](lease)


ILLEGAL = [
    (state, transition)
    for state in sorted(STATES)
    for transition in sorted(TRANSITIONS)
    if transition not in LEGAL[state]
]


@pytest.mark.parametrize(("state", "transition"), ILLEGAL)
def test_a_refused_transition_leaves_no_trace(state: str, transition: str) -> None:
    """A refused call must not move the epoch, the holder, the operator, the deadline or the log.

    An epoch that advanced on a *failed* claim would invalidate a run that was entitled to keep
    acting, and a history entry for a transition that never happened would make the audit trail lie.
    """
    lease = STATES[state]()
    before = _fingerprint(lease)

    with pytest.raises(LeaseError):
        TRANSITIONS[transition](lease)

    assert _fingerprint(lease) == before


def test_a_refusal_names_the_state_it_refused_from() -> None:
    """Enough to debug without reproducing: the error says what state the session was really in."""
    for state, transition in (("paused", "release"), ("running", "claim"), ("running", "resume")):
        lease = STATES[state]()
        with pytest.raises(LeaseError) as excinfo:
            TRANSITIONS[transition](lease)
        assert lease.state.value in str(excinfo.value), (state, transition, str(excinfo.value))


# --------------------------------------------------------- nothing reaches an undefined state


def test_no_sequence_of_calls_reaches_a_state_the_design_does_not_describe() -> None:
    """Exhaust every sequence of six operations up to length five, and hold the invariants at each.

    The claim is stronger than "the listed transitions work": it is that *nothing else is
    reachable*. Every step checks that holder agrees with state, that a deadline exists exactly
    while a human holds the session, that an operator is named only while one is involved, that the
    epoch moves by one on success and not at all on refusal, and that the audit log has one entry
    per transition.
    """
    operations: dict[str, Callable[[Lease], int]] = {
        **TRANSITIONS,
        "claim_lapsed": lambda lease: lease.claim("sam", hold_for=PAST),
    }
    names = sorted(operations)
    checked = 0

    for sequence in itertools.product(names, repeat=5):
        lease = _running()
        for name in sequence:
            state_before, expired_before, epoch_before = lease.state, lease.expired, lease.epoch
            legal_now = {
                ControlState.RUNNING: {"escalate"},
                ControlState.PAUSED: {"claim", "claim_lapsed"},
                ControlState.HUMAN_CONTROL: {"release"}
                | ({"reclaim_expired"} if expired_before else set()),
                ControlState.RESUMING: {"resume"},
            }[state_before]

            try:
                operations[name](lease)
                succeeded = True
            except LeaseError:
                succeeded = False

            assert succeeded == (name in legal_now), (sequence, name, state_before)
            assert lease.epoch == epoch_before + (1 if succeeded else 0), (sequence, name)
            assert lease.holder is HOLDER_FOR_STATE[lease.state], (sequence, name)
            assert (lease.expires_at is not None) == (lease.state is ControlState.HUMAN_CONTROL), (
                sequence,
                name,
            )
            if lease.state is ControlState.HUMAN_CONTROL:
                assert lease.operator is not None, (sequence, name)
            if lease.state in (ControlState.RUNNING, ControlState.PAUSED):
                assert lease.operator is None, f"{sequence}: a named operator on an idle session"
            assert len(lease.history) == lease.epoch - 1, (sequence, name)
            checked += 1

    assert checked == 6**5 * 5, "the walk must actually visit every sequence, or it proves nothing"


def test_the_epoch_never_goes_backwards_over_many_full_cycles() -> None:
    """Monotonic, never reset: each cycle adds exactly four, and history mirrors the epoch."""
    lease = _running()
    previous = lease.epoch
    for cycle in range(25):
        for step in (
            lambda: lease.escalate("stuck"),
            lambda: lease.claim("alex"),
            lambda: lease.release(),
            lambda: lease.resume(),
        ):
            assert step() == previous + 1
            previous = lease.epoch
        assert lease.epoch == 1 + 4 * (cycle + 1)
        assert lease.state is ControlState.RUNNING

    epochs = [int(entry.split(":", 1)[0]) for entry in lease.history]
    assert epochs == list(range(2, lease.epoch + 1)), "one history entry per epoch, in order"


def test_a_reclaimed_session_can_be_claimed_again_at_the_lease_level() -> None:
    """After a lapsed hold is reclaimed the lease is PAUSED, which a new operator may claim.

    Recorded at this level because it is the lease's half of the recovery story: the lease is not
    wedged, and a fresh operator gets a strictly newer epoch than the one who walked away.
    """
    lease = _human_lapsed()
    abandoned_epoch = lease.epoch

    lease.reclaim_expired()
    assert lease.state is ControlState.PAUSED and lease.operator is None

    new_epoch = lease.claim("sam", hold_for=FUTURE)
    assert new_epoch > abandoned_epoch
    assert lease.operator == "sam" and not lease.expired


# ------------------------------------------------------------------------------- assert_held


@pytest.mark.parametrize("delta", [-1, 0, 1, 2, 10**9, -(10**9)])
@pytest.mark.parametrize("actor", list(Actor))
@pytest.mark.parametrize("state", sorted(STATES))
def test_only_the_current_holder_at_the_current_epoch_may_act(
    state: str, actor: Actor, delta: int
) -> None:
    """`assert_held` and `held_by` agree, and both say yes for exactly one (actor, epoch) pair.

    Includes epochs from the *future* (a forged grant) and from before the beginning, not just the
    stale one: an equality check refuses all of them, and a check written as `>=` would not.
    """
    lease = STATES[state]()
    epoch = lease.epoch + delta
    allowed = not lease.expired and actor is lease.holder and delta == 0

    assert lease.held_by(actor, epoch) is allowed
    if allowed:
        lease.assert_held(actor, epoch)
    else:
        with pytest.raises(LeaseError):
            lease.assert_held(actor, epoch)


def test_a_stale_epoch_refusal_reports_both_generations() -> None:
    lease = _human()
    stale = lease.epoch - 1

    with pytest.raises(LeaseError) as excinfo:
        lease.assert_held(Actor.HUMAN, stale)

    message = str(excinfo.value)
    assert f"epoch {stale}" in message and f"current {lease.epoch}" in message
    assert "stale" in message


def test_a_lapsed_hold_refuses_even_the_holder_at_the_right_epoch() -> None:
    """The expiry exists so a session held by someone who walked away is recoverable. A check that
    only compared holder and epoch would honour the hold forever."""
    lease = _human_lapsed()
    assert lease.expired
    assert not lease.held_by(Actor.HUMAN, lease.epoch)

    with pytest.raises(LeaseError, match="expired"):
        lease.assert_held(Actor.HUMAN, lease.epoch)


def test_a_zero_length_hold_is_already_expired() -> None:
    """The boundary: `expired` is `now >= deadline`, so a hold of zero is over the moment it starts
    rather than lasting one more instant."""
    lease = _paused()
    lease.claim("alex", hold_for=timedelta(0))
    assert lease.expired


def test_a_long_hold_is_not_expired() -> None:
    lease = _paused()
    lease.claim("alex", hold_for=timedelta(days=365))
    assert not lease.expired
    lease.assert_held(Actor.HUMAN, lease.epoch)


def test_an_automation_hold_never_expires() -> None:
    """Only a human hold has a deadline. A running automation that "expired" would be reclaimed out
    from under a step in flight."""
    lease = _running()
    assert lease.expires_at is None and not lease.expired

    lease.escalate("stuck")
    lease.claim("alex")
    lease.release()
    lease.resume()
    assert lease.expires_at is None and not lease.expired
    lease.assert_held(Actor.AUTOMATION, lease.epoch)


# ------------------------------------------------------------------ hostile operator strings


@pytest.mark.parametrize(
    "operator",
    [
        "",
        "   ",
        "<script>alert(1)</script>",
        "Robert'); DROP TABLE interventions;--",
        "{0} {reason} %s %d ${jndi:ldap://x}",
        "../../etc/passwd",
        "a" * 20_000,
        (
            "e\N{COMBINING ACUTE ACCENT} \N{RIGHT-TO-LEFT OVERRIDE}evil"
            "\N{POP DIRECTIONAL FORMATTING} \N{ZERO WIDTH SPACE}zero\N{ZERO WIDTH SPACE}width"
        ),
        "\N{CYRILLIC SMALL LETTER A}dmin",  # a look-alike for the Latin letter
        "line1\nline2\r\nline3",
    ],
    ids=[
        "empty",
        "whitespace",
        "html",
        "sql",
        "format-string",
        "path-traversal",
        "very-long",
        "combining-rtl-zero-width",
        "homoglyph",
        "newlines",
    ],
)
def test_an_operator_name_is_recorded_verbatim_and_cannot_break_the_log(operator: str) -> None:
    """The lease takes the name as an opaque label (the console documents "no authentication").

    Hostile text must be stored, not interpreted: a name is interpolated into a history line and
    must not raise, truncate or reshape it. Escaping for display is the renderer's job.
    """
    lease = _paused()
    epoch = lease.claim(operator)

    assert lease.operator == operator
    assert lease.history[-1] == f"{epoch}:human_control:claimed by {operator}"
    assert lease.state is ControlState.HUMAN_CONTROL


def test_lease_errors_do_not_echo_an_operator_name() -> None:
    """A refusal is shown to whoever asked. It says what state blocked them, never who held it."""
    token = "not-a-real-operator-handle"
    lease = _paused()
    lease.claim(token)

    messages: list[str] = []
    for transition in ("escalate", "claim", "resume", "reclaim_expired"):
        with pytest.raises(LeaseError) as excinfo:
            TRANSITIONS[transition](lease)
        messages.append(str(excinfo.value))
    with pytest.raises(LeaseError) as excinfo:
        lease.assert_held(Actor.AUTOMATION, lease.epoch)
    messages.append(str(excinfo.value))

    assert all(token not in message for message in messages), messages
