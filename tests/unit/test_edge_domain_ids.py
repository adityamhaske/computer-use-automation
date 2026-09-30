"""Identifiers must survive the redactor.

Every id is written into sinks that are all redacted, and the account-number rule reads a bare run
of digits as a customer's account. A randomly drawn hex id is all digits with probability
(10/16)**length, so without a guard roughly one decision in 300 would have its id replaced by
`<redacted:account_number>` in the trace -- silently breaking the match between an authorization
and its dispatch, and surfacing as a rare flaky test rather than as the defect it is.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from cua.domain.ids import new_id
from cua.policy.config import parse_policy
from cua.policy.redact import Redactor

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"


class _Draw:
    """Stands in for the UUID `uuid4()` returns; only `.hex` is read."""

    def __init__(self, hex_digits: str) -> None:
        self.hex = hex_digits


@pytest.mark.parametrize("length", [8, 10, 12])
def test_an_id_is_the_prefix_and_exactly_the_requested_number_of_hex_characters(
    length: int,
) -> None:
    assert re.fullmatch(rf"dec-[0-9a-f]{{{length}}}", new_id("dec", length))


@pytest.mark.parametrize("length", [8, 10, 12])
def test_an_id_always_contains_a_letter_however_many_are_drawn(length: int) -> None:
    for _ in range(5_000):
        assert not new_id("x", length).split("-", 1)[1].isdigit()


def test_an_all_digit_draw_is_thrown_away_and_drawn_again(monkeypatch: pytest.MonkeyPatch) -> None:
    draws = iter([_Draw("1" * 32), _Draw("2" * 32), _Draw("a" + "1" * 31)])
    monkeypatch.setattr(uuid, "uuid4", lambda: next(draws))

    assert new_id("dec", 12) == "dec-a11111111111"


@pytest.mark.parametrize(
    "prefix_and_length",
    [("dec", 12), ("int", 10), ("rep", 10), ("disc", 10), ("sess", 10), ("invoke", 8)],
)
def test_the_shipped_redaction_policy_leaves_every_minted_id_alone(
    prefix_and_length: tuple[str, int],
) -> None:
    """The failure mode itself, end to end: mint many ids and push each through the real rules."""
    redactor = Redactor(parse_policy(POLICY.read_text(encoding="utf-8")).redaction)
    prefix, length = prefix_and_length

    for _ in range(3_000):
        identifier = new_id(prefix, length)
        assert redactor.text(identifier) == identifier, identifier
