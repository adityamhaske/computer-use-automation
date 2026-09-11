"""Values that a capability can carry: literals, input references, and secret references.

Three kinds, deliberately distinguished at the type level rather than by convention:

    "savings"                   a literal baked into the flow
    {"$input": "member_id"}     supplied by the caller per invocation
    {"$secret": "core.pw"}      resolved at dispatch, never stored anywhere

The secret case is the one that matters. A recorded discovery run has real credentials typed into
it. If those were stored as literals, they would land in the artifact, the trace, the evidence, and
every screenshot -- in a system handling regulated financial data. Making a secret a *reference*
means there is no code path that can persist the value, because the artifact never holds it.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InputRef(_Frozen):
    """A value the calling agent supplies for this invocation."""

    input_name: str = Field(alias="$input")
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class SecretRef(_Frozen):
    """A named credential resolved at dispatch time and never persisted.

    The reference is what gets written to the artifact and the evidence. The value exists only in
    memory, for the duration of one action.
    """

    secret_name: str = Field(alias="$secret")
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class OutputRef(_Frozen):
    """A value produced by an earlier step in this same run."""

    output_name: str = Field(alias="$output")
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


ValueExpr: TypeAlias = str | int | float | bool | InputRef | SecretRef | OutputRef
"""Anything a step can use as a value."""


ValueType: TypeAlias = Literal["string", "integer", "number", "boolean", "money", "date", "enum"]
"""The declared type of a capability input or output.

`money` and `date` are first-class rather than `string` because this is a banking domain: a caller
that receives a balance should not have to guess whether "$4,210.55" is a number, and an extractor
that produces one should say so.
"""


def is_reference(value: ValueExpr) -> bool:
    """True if this value must be resolved before use (rather than used as-is)."""
    return isinstance(value, InputRef | SecretRef | OutputRef)


def is_secret(value: ValueExpr) -> bool:
    """True if this value must never reach a log, artifact, screenshot, or model prompt."""
    return isinstance(value, SecretRef)


AnyRef = Annotated[InputRef | SecretRef | OutputRef, Field(union_mode="left_to_right")]
