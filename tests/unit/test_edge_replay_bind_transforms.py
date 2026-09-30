"""Edge cases for value binding and output transforms.

Two small modules that sit on either side of the page: `bind` turns a stored *reference* into the
value typed into it, at the last possible moment, and `transforms` turns what was read back into the
shape the artifact's published contract promised. Both are where a confident wrong value is born --
the module docstring in `bind.py` describes a reference object being stringified into a field -- so
the tests here pin the boundary behaviour rather than the happy path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cua.domain.action import Assert, Click, Extract, PressKey, Select, Type, WaitFor
from cua.domain.capability import OutputSpec
from cua.domain.predicates import NodeExists, NodeQuery
from cua.domain.target import NameMatch, TargetDescriptor
from cua.domain.values import InputRef, OutputRef, SecretRef
from cua.policy.config import RedactionPolicy, parse_policy
from cua.policy.redact import Redactor
from cua.policy.secrets import SecretNotFoundError, SecretResolver
from cua.replay.bind import UnboundReferenceError, bind_action, resolve_value
from cua.replay.transforms import TRANSFORMS, UnknownTransformError, apply_transform

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"
FIELD = TargetDescriptor(role="textbox", name=NameMatch(value="Member Number"))


def parse_redaction() -> RedactionPolicy:
    return parse_policy(POLICY.read_text()).redaction


def input_ref(name: str) -> InputRef:
    return InputRef.model_validate({"$input": name})


def output_ref(name: str) -> OutputRef:
    return OutputRef.model_validate({"$output": name})


def secret_ref(name: str) -> SecretRef:
    return SecretRef.model_validate({"$secret": name})


# ================================================================== resolve_value


@pytest.mark.parametrize("literal", ["savings", "", 0, 7, 0.0, False, True, "{$input: x}"])
def test_a_literal_is_returned_untouched_including_falsy_ones(literal: Any) -> None:
    """Falsy literals (0, "", False) are values, not absences -- and a string that merely *looks*
    like a reference is still just a string."""
    assert resolve_value(literal, inputs={"x": "other"}) is literal


def test_an_input_reference_reads_the_callers_value_even_when_it_is_empty() -> None:
    """Present-but-empty is a supplied value. Treating "" as unbound would turn an empty optional
    field into a run failure, and treating it as missing would hide that the caller sent nothing."""
    assert resolve_value(input_ref("memo"), inputs={"memo": ""}) == ""


def test_an_unsupplied_input_reference_names_only_what_is_missing() -> None:
    """The error says which reference is unbound -- and does not dump the values that *are* bound,
    which may be another input the caller declared sensitive."""
    with pytest.raises(UnboundReferenceError) as caught:
        resolve_value(input_ref("memo"), inputs={"pin": "s3cret-pin-value"})

    message = str(caught.value)
    assert "memo" in message
    assert "s3cret-pin-value" not in message


def test_an_output_reference_needs_an_earlier_step_to_have_produced_it() -> None:
    assert (
        resolve_value(output_ref("balance"), inputs={}, outputs={"balance": "4210.55"}) == "4210.55"
    )

    for outputs in (None, {}, {"other": "1"}):
        with pytest.raises(UnboundReferenceError, match="no earlier step produced"):
            resolve_value(output_ref("balance"), inputs={}, outputs=outputs)


def test_an_empty_string_output_is_still_a_produced_output() -> None:
    """`or {}` in the resolver must not swallow a falsy *mapping* into "nothing produced" -- and
    a falsy *value* inside it is still present."""
    assert resolve_value(output_ref("memo"), inputs={}, outputs={"memo": ""}) == ""


def test_a_secret_reference_without_a_resolver_fails_closed_and_names_the_secret() -> None:
    with pytest.raises(UnboundReferenceError, match="no secret resolver is configured") as caught:
        resolve_value(secret_ref("core.password"), inputs={}, secrets=None)

    assert "core.password" in str(caught.value)


def test_a_secret_reference_resolves_through_the_resolver_and_is_registered_for_scrubbing() -> None:
    """The value reaches the caller *and* the redactor learns it in the same call -- resolution is
    the single point every secret passes through, so no path can obtain one unregistered."""
    redactor = Redactor(parse_redaction())
    resolver = SecretResolver(
        overrides={"core.password": "correct-horse-battery"}, redactor=redactor
    )

    value = resolve_value(secret_ref("core.password"), inputs={}, secrets=resolver)

    assert value == "correct-horse-battery"
    assert "correct-horse-battery" not in redactor.text(f"typed {value}")


def test_an_unconfigured_secret_is_fatal_at_the_resolver() -> None:
    """Documented as deliberately fatal; asserted so the contract `bind` relies on is explicit."""
    with pytest.raises(SecretNotFoundError):
        resolve_value(secret_ref("nope.missing"), inputs={}, secrets=SecretResolver())


# ==================================================================== bind_action


def test_binding_copies_and_leaves_the_declared_reference_intact() -> None:
    """A capability is immutable and shared. Binding for one caller must not rewrite the step for
    the next one, or the second caller silently types the first caller's member number."""
    declared = Type(target=FIELD, value=input_ref("member_id"))

    first = bind_action(declared, inputs={"member_id": "11111"})
    second = bind_action(declared, inputs={"member_id": "22222"})

    assert isinstance(first, Type) and isinstance(second, Type)
    assert (first.value, second.value) == ("11111", "22222")
    assert declared.value == input_ref("member_id")
    assert first is not declared


@pytest.mark.parametrize("value", ["", 0, False, 12])
def test_a_falsy_literal_value_is_bound_not_mistaken_for_no_value(value: Any) -> None:
    """`raw is None`, not truthiness: typing "" (clearing a field) is a legitimate step."""
    bound = bind_action(Type(target=FIELD, value=value), inputs={})

    assert isinstance(bound, Type)
    assert bound.value == str(value)


def test_select_values_are_bound_like_type_values() -> None:
    bound = bind_action(Select(target=FIELD, value=input_ref("kind")), inputs={"kind": "savings"})

    assert isinstance(bound, Select)
    assert bound.value == "savings"


@pytest.mark.parametrize(
    "action",
    [
        Click(target=FIELD),
        PressKey(key="Enter"),
        Extract(target=FIELD, into="balance"),
        Assert(that=NodeExists(query=NodeQuery(role="cell"))),
        WaitFor(until=NodeExists(query=NodeQuery(role="cell"))),
    ],
    ids=["click", "press_key", "extract", "assert", "wait_for"],
)
def test_an_action_with_no_value_passes_through_as_the_same_object(action: Any) -> None:
    """Nothing to bind means nothing is copied -- and nothing can be unbound, so no inputs are
    needed for it."""
    assert bind_action(action, inputs={}) is action


def test_a_value_that_looks_like_a_reference_is_typed_literally() -> None:
    """A caller's *data* is never re-resolved. `{$secret: core.password}` supplied as an input must
    reach the field as that text, not as the password."""
    resolver = SecretResolver(overrides={"core.password": "correct-horse-battery"})

    bound = bind_action(
        Type(target=FIELD, value=input_ref("memo")),
        inputs={"memo": "{$secret: core.password}"},
        secrets=resolver,
    )

    assert isinstance(bound, Type)
    assert bound.value == "{$secret: core.password}"
    assert resolver.issued_values == frozenset(), "no secret may have been resolved"


def test_an_unbound_reference_stops_the_bind_rather_than_typing_the_reference() -> None:
    """The bug this module exists to fix: the reference object was stringified and typed."""
    with pytest.raises(UnboundReferenceError):
        bind_action(Type(target=FIELD, value=input_ref("member_id")), inputs={})


def test_a_bound_secret_is_a_plain_string_only_in_the_returned_copy() -> None:
    declared = Type(target=FIELD, value=secret_ref("core.password"))
    resolver = SecretResolver(overrides={"core.password": "correct-horse-battery"})

    bound = bind_action(declared, inputs={}, secrets=resolver)

    assert isinstance(bound, Type) and bound.value == "correct-horse-battery"
    assert "correct-horse-battery" not in repr(declared), "the artifact never holds the value"


# ===================================================================== transforms


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("$4,210.55", "4210.55"),
        ("4,210.55", "4210.55"),
        ("4210.55", "4210.55"),
        ("  $4,210.55  ", "4210.55"),
        ("$1,000,000.00", "1000000.00"),
        ("$0.00", "0.00"),
        ("$0.5", "0.5"),
        ("-$12.30", "-12.30"),
        ("$-12.30", "-12.30"),
        ("USD 5.00", "5.00"),
        ("\N{EURO SIGN}1 234.50", "1234.50"),
        ("$\N{NO-BREAK SPACE}4,210.55", "4210.55"),
    ],
)
def test_money_keeps_digits_point_and_sign_and_drops_everything_else(
    raw: str, canonical: str
) -> None:
    assert apply_transform("money", raw) == canonical


@pytest.mark.parametrize("raw", ["$4,210.55", "-$12.30", "USD 5.00", "0.00", "N/A", "$"])
def test_money_is_idempotent(raw: str) -> None:
    """Normalizing an already-normal value must be a no-op, or re-running a transform (a resumed
    run, a re-read) would drift the answer."""
    once = apply_transform("money", raw)

    assert apply_transform("money", once) == once


@pytest.mark.parametrize("raw", ["N/A", "--", "\N{EM DASH}", "$", "pending", "  n/a  "])
def test_money_never_turns_non_empty_text_into_an_empty_output(raw: str) -> None:
    """The extraction guard refuses an empty read; the transform must not manufacture one after it.

    Text with no digits in it falls back to itself (trimmed) rather than to "", so a caller sees
    what the screen said instead of a blank that looks like a value.
    """
    assert apply_transform("money", raw) != ""


@pytest.mark.parametrize(
    ("raw", "iso"),
    [
        ("3/9/2026", "2026-03-09"),
        ("03/09/2026", "2026-03-09"),
        ("12/31/1999", "1999-12-31"),
        ("1/1/0001", "0001-01-01"),
        ("  3/9/2026  ", "2026-03-09"),
        ("3/9/2026\n", "2026-03-09"),
    ],
)
def test_date_reads_us_order_into_zero_padded_iso(raw: str, iso: str) -> None:
    assert apply_transform("date", raw) == iso


@pytest.mark.parametrize(
    "raw",
    [
        "2026-03-09",
        "March 9, 2026",
        "3/9/26",
        "9 Mar 2026",
        "3/9",
        "3-9-2026",
        "",
        "3/9/2026 14:00",
    ],
)
def test_date_leaves_anything_it_cannot_read_as_us_order_alone(raw: str) -> None:
    """No guessing: "3/9/26" could be 2026 or 1926, and "3-9-2026" could be either order. A
    normalizer that guesses a century or a month order hands the caller a wrong date in ISO
    clothing, which is worse than handing them what the screen said."""
    assert apply_transform("date", raw) == raw.strip()


@pytest.mark.parametrize("raw", ["3/9/2026", "2026-03-09", "March 9, 2026"])
def test_date_is_idempotent(raw: str) -> None:
    once = apply_transform("date", raw)

    assert apply_transform("date", once) == once


@pytest.mark.parametrize("name", ["currency", "MONEY", "Money", "money ", "", "iso_date", "none"])
def test_an_unknown_transform_is_refused_rather_than_returning_the_raw_value(name: str) -> None:
    """One deployment must not hand the caller "$4,210.55" while another hands them "4210.55"."""
    with pytest.raises(UnknownTransformError) as caught:
        apply_transform(name, "$4,210.55")

    message = str(caught.value)
    assert repr(name) in message
    assert "['date', 'money']" in message, "the error must list what this executor does implement"


def test_every_format_the_published_output_schema_promises_has_a_transform() -> None:
    """`OutputSpec.json_schema()` tells a calling agent `format: money|date`. A format with no
    transform behind it is a label, not a promise -- which is how this module came to exist."""
    promised = {
        OutputSpec(name="x", type=kind).json_schema().get("format")  # type: ignore[arg-type]
        for kind in ("money", "date", "string", "integer")
    } - {None}

    assert promised <= set(TRANSFORMS)
