"""Validating a caller's arguments before anything touches the application.

A malformed member id is caught here, as `INPUT_VALIDATION_FAILED`, rather than three steps into the
flow as a confusing "no records found". That distinction matters to the caller: one is their bug and
the other is an answer about a member.
"""

from __future__ import annotations

import re
from typing import Any

from cua.domain.capability import Capability


class InputValidationError(ValueError):
    """The caller's arguments do not satisfy the capability's declared contract."""


# Only the types with an unambiguous textual form. `money` and `date` are deliberately left to the
# application and the capability's own `pattern`: their accepted spellings ("$4,210.55",
# "09/11/2026") are the application's, not something this layer should guess.
_TYPE_SHAPES: dict[str, str] = {
    "integer": r"[+-]?[0-9]+",
    "number": r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?",
    "boolean": r"(?i:true|false)",
}


def validate_inputs(capability: Capability, supplied: dict[str, Any]) -> dict[str, Any]:
    """Check and normalize the caller's arguments against the declared inputs."""
    declared = {spec.name: spec for spec in capability.inputs}

    unknown = set(supplied) - set(declared)
    if unknown:
        # Refused rather than ignored: a caller passing `member_no` when the capability declares
        # `member_number` has made a mistake, and silently running with a missing argument would
        # return a confident answer about the wrong thing.
        raise InputValidationError(
            f"unknown input(s) {sorted(unknown)}; this capability declares {sorted(declared)}"
        )

    resolved: dict[str, Any] = {}
    for name, spec in declared.items():
        # None means "no value", as it already does when a default is chosen below. A JSON
        # `null` from an agent must not be typed into the application as the letters "None".
        if supplied.get(name) is not None:
            value = supplied[name]
        elif spec.default is not None:
            value = spec.default
        elif spec.required:
            raise InputValidationError(f"missing required input {name!r}")
        else:
            continue

        text = str(value)
        # The published tool schema says `integer`; replay has to refuse what that schema forbids.
        # ASCII digits only: `int()` and `\d` both accept every script's digits.
        shape = _TYPE_SHAPES.get(spec.type)
        if shape is not None and not re.fullmatch(shape, text):
            raise InputValidationError(f"input {name!r} = {text!r} is not a valid {spec.type}")
        if spec.pattern and not re.fullmatch(spec.pattern, text):
            raise InputValidationError(
                f"input {name!r} = {text!r} does not match the declared pattern {spec.pattern!r}"
            )
        if spec.enum and text not in spec.enum:
            raise InputValidationError(f"input {name!r} = {text!r} is not one of {list(spec.enum)}")
        resolved[name] = text

    return resolved
