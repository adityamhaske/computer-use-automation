"""Deriving names and types from what a discovery run actually observed.

Everything here is inference, and inference is where a compiler quietly makes things up. The rule
followed throughout: derive from something concrete that was seen, or do not derive at all. A
plausible-looking guess is worse than an obvious gap, because a gap gets reviewed and a guess gets
trusted.
"""

from __future__ import annotations

import re

from cua.domain.values import ValueType

MONEY = re.compile(r"^-?\$-?[\d,]+\.\d{2}$|^-?[\d]{1,3}(?:,\d{3})+\.\d{2}$")
"""Requires a currency symbol or thousands grouping.

A bare `4210.55` is left as `number`: two decimal places alone is equally consistent with an
interest rate or a fee multiplier, and a type is a claim to a calling agent rather than a hint."""
DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$|^\d{4}-\d{2}-\d{2}$")
INTEGER = re.compile(r"^-?\d+$")
NUMBER = re.compile(r"^-?\d+\.\d+$")


def snake(text: str) -> str:
    """A stable identifier from a human label. 'Member Number:' -> 'member_number'."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip()).strip("_").lower()
    return re.sub(r"_+", "_", cleaned) or "value"


def infer_type(value: str) -> ValueType:
    """The declared type of an extracted value, from its shape.

    `money` and `date` are worth distinguishing from `string` because this is a banking domain: a
    calling agent that receives a balance should not have to guess whether "$4,210.55" is a number,
    and an extractor that produces one should say so.
    """
    text = value.strip()
    if MONEY.match(text):
        return "money"
    if DATE.match(text):
        return "date"
    if INTEGER.match(text):
        return "integer"
    if NUMBER.match(text):
        return "number"
    return "string"


def shape_pattern(value: str) -> str | None:
    """A regex matching values of the same shape, for a checkpoint assertion.

    This is what makes an inferred checkpoint *goal-specific* rather than trivially true. Asserting
    "a heading exists" passes on almost any page; asserting "a cell holding a currency-formatted
    amount exists" is a real statement about having reached a member's balance.

    Returns None when the shape is not distinctive -- an arbitrary string tells a checkpoint
    nothing, and a pattern matching everything is worse than no pattern because it looks like
    verification.
    """
    text = value.strip()
    if MONEY.match(text):
        return r"^\$?-?[0-9,]+\.[0-9]{2}$"
    if DATE.match(text):
        return r"^\d{1,2}/\d{1,2}/\d{4}$|^\d{4}-\d{2}-\d{2}$"
    if INTEGER.match(text) and len(text) >= 4:
        return rf"^\d{{{len(text)}}}$"
    return None


def input_pattern(value: str) -> str | None:
    """A validation pattern for a lifted input parameter.

    Deliberately loose on length: a run saw one member number, and inferring `^\\d{5}$` from a
    single five-digit example would reject a six-digit member on the first real call. The digits
    are a real observation; the exact width is a sample size of one.
    """
    text = value.strip()
    if INTEGER.match(text):
        return r"^[0-9]{4,12}$"
    return None


def capability_id(vendor_product: str, goal: str, outputs: list[str]) -> str:
    """`<product>.<subject>.<operation>` -- namespaced by vendor product, never by tenant.

    That namespacing is the precondition for cross-tenant reuse (ADR 0005): an id containing an
    institution's name could never be shared with the next one running the same software.
    """
    operation = snake(outputs[0]) if outputs else snake(goal.split()[0] if goal else "flow")
    return f"{snake(vendor_product)}.{operation}"
