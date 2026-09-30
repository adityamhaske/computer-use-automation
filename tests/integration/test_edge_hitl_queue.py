"""The intervention queue: ordering, illegal state changes, and the card an operator reads.

In-memory and clock-free. Creation times are assigned explicitly so ordering is asserted rather than
left to whatever the wall clock happened to do between two constructor calls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cua.hitl.intervention import InterventionQueue, InterventionRequest, InterventionState

EPOCH0 = datetime(2026, 1, 1, tzinfo=UTC)


def _request(ident: str, *, age_minutes: int = 0, **overrides: object) -> InterventionRequest:
    """`age_minutes` is how long ago it stopped; a bigger number is an older request."""
    fields: dict = {
        "intervention_id": ident,
        "session_id": "sess-1",
        "run_id": "run-1",
        "capability_ref": "corebank.member.savings_balance@1.0.0",
        "goal": "Read a balance",
        "reason": "the resolver refused an ambiguous control",
        "created_at": EPOCH0 - timedelta(minutes=age_minutes),
    }
    fields.update(overrides)
    return InterventionRequest(**fields)


def _in_state(queue: InterventionQueue, ident: str, state: InterventionState):
    """Drive a request into `state` through the queue's own methods."""
    queue.open(_request(ident))
    if state is InterventionState.OPEN:
        return
    if state is InterventionState.ABANDONED:
        queue.abandon(ident, "nobody came")
        return
    queue.claim(ident, "alex")
    if state is InterventionState.CLAIMED:
        return
    queue.release(ident, human_actions=1, human_delta="changed one field")
    if state is InterventionState.RELEASED:
        return
    queue.resolve(ident, "automation resumed")


# ------------------------------------------------------------------------ ordering


def test_pending_is_oldest_first_whatever_order_requests_arrived_in() -> None:
    """A stuck run nobody looked at for an hour outranks one that just stopped.

    Insertion order is the trap: a request that *stopped* earlier can be *opened* later (a slow
    escalation), and a queue that returned insertion order would put it behind fresher work.
    """
    queue = InterventionQueue()
    queue.open(_request("new", age_minutes=1))
    queue.open(_request("oldest", age_minutes=90))
    queue.open(_request("middle", age_minutes=30))

    assert [r.intervention_id for r in queue.pending()] == ["oldest", "middle", "new"]


def test_requests_with_identical_timestamps_keep_a_stable_order() -> None:
    """Ties must not reshuffle between polls, or an operator's queue flickers while they read it."""
    queue = InterventionQueue()
    for ident in ("a", "b", "c", "d"):
        queue.open(_request(ident, age_minutes=5))

    first = [r.intervention_id for r in queue.pending()]
    second = [r.intervention_id for r in queue.pending()]
    assert first == second == ["a", "b", "c", "d"]


_TAKEN_OR_FINISHED = [
    s for s in InterventionState if s not in (InterventionState.OPEN, InterventionState.ABANDONED)
]


@pytest.mark.parametrize("state", _TAKEN_OR_FINISHED, ids=str)
def test_only_open_or_abandoned_requests_are_pending(state: InterventionState) -> None:
    """Claimed, released and resolved work is not waiting for an operator."""
    queue = InterventionQueue()
    _in_state(queue, "done", state)
    queue.open(_request("waiting", age_minutes=1))

    assert [r.intervention_id for r in queue.pending()] == ["waiting"]


def test_an_abandoned_request_returns_to_the_queue_flagged_and_can_be_claimed() -> None:
    """An expired hold leaves its session PAUSED. If the request then dropped out of the queue,
    nothing could claim it again and the expiry meant to free the session would strand it."""
    queue = InterventionQueue()
    _in_state(queue, "lapsed", InterventionState.ABANDONED)
    queue.open(_request("waiting", age_minutes=1))

    assert {r.intervention_id for r in queue.pending()} == {"lapsed", "waiting"}
    claimed = queue.claim("lapsed", "sam")
    assert claimed.state is InterventionState.CLAIMED and claimed.operator == "sam"


def test_an_empty_queue_has_nothing_pending() -> None:
    assert InterventionQueue().pending() == []


# ------------------------------------------------------------------ illegal transitions


@pytest.mark.parametrize("state", _TAKEN_OR_FINISHED, ids=str)
def test_only_an_open_or_abandoned_request_can_be_claimed(state: InterventionState) -> None:
    """Claiming something already taken or finished is a refused claim, and the loser does not
    displace whoever already holds it."""
    queue = InterventionQueue()
    _in_state(queue, "x", state)
    before = queue.get("x")
    assert before is not None
    holder, claimed_at = before.operator, before.claimed_at

    with pytest.raises(ValueError, match=state.value):
        queue.claim("x", "sam")

    after = queue.get("x")
    assert after is not None and after.state is state
    assert (after.operator, after.claimed_at) == (holder, claimed_at)


@pytest.mark.parametrize(
    "state",
    [s for s in InterventionState if s is not InterventionState.CLAIMED],
    ids=str,
)
def test_only_a_claimed_request_can_be_released(state: InterventionState) -> None:
    queue = InterventionQueue()
    _in_state(queue, "x", state)

    with pytest.raises(ValueError, match="not claimed"):
        queue.release("x", human_actions=3, human_delta="anything")

    request = queue.get("x")
    assert request is not None and request.state is state
    assert request.human_actions != 3, "a refused release must not record its numbers"


@pytest.mark.parametrize("call", ["claim", "release", "resolve", "abandon"])
def test_acting_on_an_unknown_request_raises_key_error(call: str) -> None:
    """The queue's own contract for a missing id is a `KeyError` that names the id -- callers (the
    console) are expected to translate it, which `test_edge_hitl_console_api.py` holds them to."""
    queue = InterventionQueue()
    calls = {
        "claim": lambda: queue.claim("ghost", "alex"),
        "release": lambda: queue.release("ghost", human_actions=0, human_delta=""),
        "resolve": lambda: queue.resolve("ghost"),
        "abandon": lambda: queue.abandon("ghost"),
    }
    with pytest.raises(KeyError, match="ghost"):
        calls[call]()


def test_get_returns_none_for_an_unknown_id_rather_than_raising() -> None:
    assert InterventionQueue().get("ghost") is None


def test_the_full_lifecycle_records_who_did_what_and_when() -> None:
    queue = InterventionQueue()
    queue.open(_request("x", age_minutes=10))

    claimed = queue.claim("x", "alex")
    assert claimed.state is InterventionState.CLAIMED and claimed.operator == "alex"
    assert claimed.claimed_at is not None and claimed.claimed_at > claimed.created_at

    released = queue.release("x", human_actions=4, human_delta="+1/-0 nodes")
    assert released.state is InterventionState.RELEASED
    assert (released.human_actions, released.human_delta) == (4, "+1/-0 nodes")
    assert released.released_at is not None and released.released_at >= claimed.claimed_at

    resolved = queue.resolve("x", "automation resumed")
    assert resolved.state is InterventionState.RESOLVED
    assert resolved.resolution_note == "automation resumed"


def test_abandoning_records_why_so_the_next_operator_can_tell_it_from_untouched_work() -> None:
    queue = InterventionQueue()
    _in_state(queue, "x", InterventionState.CLAIMED)

    abandoned = queue.abandon("x", "the hold by alex expired before it was released")

    assert abandoned.state is InterventionState.ABANDONED
    assert "expired" in abandoned.resolution_note
    assert abandoned.operator == "alex", (
        "who had it is part of what the next operator needs to know"
    )


# ------------------------------------------------------------------------ the context card

CARD_KEYS = {
    "intervention",
    "capability",
    "goal",
    "stopped_at",
    "because",
    "screenshot",
    "snapshot",
    "evidence",
}


def test_the_card_has_exactly_the_keys_the_console_reads() -> None:
    """The card is an interface: the operator page reads these names and nothing else, so a rename
    here breaks the screen without a single Python test noticing. The JS side of this contract is
    checked in `tests/invariants/test_edge_hitl_console_never_injects.py`."""
    assert set(_request("x").context_card()) == CARD_KEYS


def test_the_card_carries_no_operator_state() -> None:
    """The card is what the *queue* shows. Who holds it, and how far it got, is lease state and is
    not something a person deciding whether to claim should be shown as fact."""
    queue = InterventionQueue()
    _in_state(queue, "x", InterventionState.RESOLVED)
    card = queue.requests["x"].context_card()

    assert "alex" not in repr(card)
    assert set(card) == CARD_KEYS


def test_optional_card_fields_are_none_not_missing_or_the_string_none() -> None:
    card = _request("x").context_card()

    assert card["stopped_at"] is None
    assert card["screenshot"] is None and card["snapshot"] is None and card["evidence"] is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "<img src=x onerror=alert(1)>",
        "'; DROP TABLE interventions; --",
        "{0} {goal} %s %(reason)s ${jndi:ldap://x/a}",
        "../../../etc/passwd",
        "line1\nline2\r\n\ttabbed",
        "e\N{COMBINING ACUTE ACCENT}\N{COMBINING DIAERESIS} \N{ZERO WIDTH JOINER}",
        "\N{RIGHT-TO-LEFT OVERRIDE}drowssap\N{POP DIRECTIONAL FORMATTING}",
        "\N{CYRILLIC SMALL LETTER A}dmin \N{FULLWIDTH DIGIT ONE}\N{FULLWIDTH DIGIT TWO}",
        "x" * 100_000,
    ],
    ids=[
        "empty",
        "whitespace",
        "html",
        "sql",
        "template",
        "traversal",
        "control-characters",
        "combining-marks",
        "rtl-override",
        "homoglyph-and-fullwidth",
        "very-long",
    ],
)
def test_the_card_hands_text_over_verbatim(text: str) -> None:
    """The queue stores and returns text; it neither interprets nor reshapes it.

    Escaping belongs to the renderer and is tested there. A queue that "sanitised" on the way in
    would hide what a page actually said from the one person asked to judge it.
    """
    card = _request("x", goal=text, reason=text, step_id=text).context_card()

    assert card["goal"] == text
    assert card["because"] == text
    assert card["stopped_at"] == text


def test_reopening_the_same_id_is_visible_in_the_queue_not_silently_duplicated() -> None:
    """One id is one request: `pending()` can never list the same intervention twice."""
    queue = InterventionQueue()
    queue.open(_request("x", age_minutes=10))
    queue.open(_request("x", age_minutes=5))

    assert [r.intervention_id for r in queue.pending()] == ["x"]
