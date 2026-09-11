"""Resolving an action's value references immediately before dispatch.

A capability stores `{$input: member_id}` rather than a literal -- that is what makes it reusable.
Something has to turn the reference back into a value at the last possible moment, and this is it.

**Last possible moment is the point.** A `{$secret: ...}` reference is resolved here, inside the
dispatch, and the value exists only for the duration of that one action. It is never written to the
artifact, the trace, a snapshot or a screenshot, because it never exists anywhere those are produced
from. That is a stronger guarantee than "we remember to redact it", which holds only until someone
adds a new sink.

The bug this module fixes was instructive: without it the executor passed the *reference object* to
the driver, which stringified it and typed `input_name` into the member-number field. The field's
`maxlength` truncated it to exactly ten characters, so the symptom was a plausible-looking wrong
value rather than an obvious crash -- and the run failed two steps later at a postcondition. Caught
by that postcondition, which is precisely the job a postcondition exists to do.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cua.domain.action import Action
from cua.domain.values import InputRef, OutputRef, SecretRef, ValueExpr
from cua.policy.secrets import SecretResolver


class UnboundReferenceError(ValueError):
    """A value referenced something the run cannot supply."""


def resolve_value(
    value: ValueExpr,
    *,
    inputs: Mapping[str, Any],
    outputs: Mapping[str, Any] | None = None,
    secrets: SecretResolver | None = None,
) -> Any:
    """Turn one value expression into a concrete value."""
    if isinstance(value, InputRef):
        if value.input_name not in inputs:
            raise UnboundReferenceError(
                f"step references input {value.input_name!r}, which the caller did not supply"
            )
        return inputs[value.input_name]

    if isinstance(value, OutputRef):
        available = outputs or {}
        if value.output_name not in available:
            raise UnboundReferenceError(
                f"step references output {value.output_name!r}, which no earlier step produced"
            )
        return available[value.output_name]

    if isinstance(value, SecretRef):
        if secrets is None:
            raise UnboundReferenceError(
                f"step references secret {value.secret_name!r} but no secret resolver is configured"
            )
        return secrets.resolve(value)

    return value


def bind_action(
    action: Action,
    *,
    inputs: Mapping[str, Any],
    outputs: Mapping[str, Any] | None = None,
    secrets: SecretResolver | None = None,
) -> Action:
    """Return a copy of `action` with its value references resolved.

    A copy, not a mutation: the capability is immutable, and binding the same step for a second
    caller must start from the same declared reference rather than from the previous caller's value.
    """
    raw = getattr(action, "value", None)
    if raw is None:
        return action

    bound = resolve_value(raw, inputs=inputs, outputs=outputs, secrets=secrets)
    return action.model_copy(update={"value": str(bound)})
