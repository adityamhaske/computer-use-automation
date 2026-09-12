"""The assertion language a capability uses to describe what it expects to see.

One small, closed, declarative vocabulary serves four jobs that are usually implemented four
different ways:

    step.precondition     what must be true before acting
    step.postcondition    what must be true after
    checkpoint            did we actually reach the goal state
    outcomes[].detect     is this a declared business outcome
    recovery[].detect     is this a declared recoverable condition

Using one vocabulary for all five is deliberate. It means "how do you know you succeeded" and "how
do you know the member wasn't found" are expressed in the same reviewable terms, and it means the
executor has exactly one evaluator to be correct about.

Evaluation is **pure**: a predicate is a function of a `UiSnapshot` and the run's inputs. No I/O, no
clock, no network -- so a capability's logic is testable without a browser, and so the same
predicate cannot behave differently on two runs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from cua.domain.snapshot import NodeScope, UiNode, UiSnapshot
from cua.domain.values import InputRef, OutputRef, ValueExpr


def _normalize(text: str) -> str:
    """Fold case, whitespace and punctuation for tolerant comparison.

    Absorbs cosmetic differences ("Member  Number" / "member number:"). Deliberately does NOT
    absorb rebranding -- "Member #" normalizes to "member #", which still does not equal
    "member number". Guessing they are the same is how automation clicks the wrong control.
    """
    return re.sub(r"[^a-z0-9 ]+", "", text.lower().replace("\xa0", " ")).strip()


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class NodeQuery(BaseModel):
    """Which nodes a predicate is talking about.

    Shared by the node predicates so that "exists", "absent" and "has value" cannot drift apart in
    how they select nodes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str | None = None
    name: str | None = None
    name_contains: str | None = None
    name_matches: str | None = None
    scope: NodeScope | None = None

    def matches(self, node: UiNode) -> bool:
        if self.role is not None and node.role != self.role:
            return False
        if self.scope is not None:
            if self.scope.frame is not None and node.scope.frame != self.scope.frame:
                return False
            if self.scope.region is not None and node.scope.region != self.scope.region:
                return False
        node_name = node.name or ""
        if self.name is not None and _normalize(node_name) != _normalize(self.name):
            return False
        if self.name_contains is not None and _normalize(self.name_contains) not in _normalize(
            node_name
        ):
            return False
        return self.name_matches is None or bool(re.search(self.name_matches, _squash(node_name)))

    def select(self, snapshot: UiSnapshot) -> tuple[UiNode, ...]:
        return tuple(n for n in snapshot.nodes if self.matches(n))

    def describe(self) -> str:
        bits = [self.role or "node"]
        if self.name:
            bits.append(f'named "{self.name}"')
        if self.name_contains:
            bits.append(f'containing "{self.name_contains}"')
        if self.name_matches:
            bits.append(f"matching /{self.name_matches}/")
        return " ".join(bits)


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class NodeExists(_Base):
    kind: Literal["node_exists"] = Field(default="node_exists", alias="assert")
    query: NodeQuery = Field(default_factory=NodeQuery)
    min_count: int = 1

    def evaluate(self, snapshot: UiSnapshot, inputs: Mapping[str, object]) -> bool:
        return len(self.query.select(snapshot)) >= self.min_count

    def describe(self) -> str:
        return f"{self.query.describe()} exists"


class NodeAbsent(_Base):
    kind: Literal["node_absent"] = Field(default="node_absent", alias="assert")
    query: NodeQuery = Field(default_factory=NodeQuery)

    def evaluate(self, snapshot: UiSnapshot, inputs: Mapping[str, object]) -> bool:
        return not self.query.select(snapshot)

    def describe(self) -> str:
        return f"{self.query.describe()} is absent"


class NodeHasValue(_Base):
    """A control holds an expected value -- typically used as a postcondition after typing.

    This is what turns "we sent keystrokes" into "the field actually contains what we sent", which
    matters on legacy screens that silently reformat or truncate input.
    """

    kind: Literal["node_has_value"] = Field(default="node_has_value", alias="assert")
    query: NodeQuery = Field(default_factory=NodeQuery)
    value: ValueExpr

    def evaluate(self, snapshot: UiSnapshot, inputs: Mapping[str, object]) -> bool:
        expected = _resolve(self.value, inputs)
        if expected is None:
            return False
        return any(
            _squash(n.value or "") == _squash(str(expected)) for n in self.query.select(snapshot)
        )

    def describe(self) -> str:
        return f"{self.query.describe()} has value {self.value!r}"


class TextPresent(_Base):
    kind: Literal["text_present"] = Field(default="text_present", alias="assert")
    value: str

    def evaluate(self, snapshot: UiSnapshot, inputs: Mapping[str, object]) -> bool:
        return _normalize(self.value) in _normalize(snapshot.text_content())

    def describe(self) -> str:
        return f'text "{self.value}" is present'


class TextAbsent(_Base):
    kind: Literal["text_absent"] = Field(default="text_absent", alias="assert")
    value: str

    def evaluate(self, snapshot: UiSnapshot, inputs: Mapping[str, object]) -> bool:
        return _normalize(self.value) not in _normalize(snapshot.text_content())

    def describe(self) -> str:
        return f'text "{self.value}" is absent'


class UrlMatches(_Base):
    kind: Literal["url_matches"] = Field(default="url_matches", alias="assert")
    pattern: str

    def evaluate(self, snapshot: UiSnapshot, inputs: Mapping[str, object]) -> bool:
        return re.search(self.pattern, snapshot.url) is not None

    def describe(self) -> str:
        return f"url matches /{self.pattern}/"


class HttpStatusIn(_Base):
    """Match on the transport status rather than on page text.

    Worth having as its own predicate: a 502 recovery rule that matched on the words "Service
    Unavailable" would also fire on a member whose name happened to contain them.
    """

    kind: Literal["http_status_in"] = Field(default="http_status_in", alias="assert")
    codes: tuple[int, ...]

    def evaluate(self, snapshot: UiSnapshot, inputs: Mapping[str, object]) -> bool:
        return snapshot.http_status in self.codes

    def describe(self) -> str:
        return f"http status in {list(self.codes)}"


class AllOf(_Base):
    kind: Literal["all_of"] = Field(default="all_of", alias="assert")
    of: tuple[Predicate, ...]

    def evaluate(self, snapshot: UiSnapshot, inputs: Mapping[str, object]) -> bool:
        return all(p.evaluate(snapshot, inputs) for p in self.of)

    def describe(self) -> str:
        return "(" + " and ".join(p.describe() for p in self.of) + ")"


class AnyOf(_Base):
    kind: Literal["any_of"] = Field(default="any_of", alias="assert")
    of: tuple[Predicate, ...]

    def evaluate(self, snapshot: UiSnapshot, inputs: Mapping[str, object]) -> bool:
        return any(p.evaluate(snapshot, inputs) for p in self.of)

    def describe(self) -> str:
        return "(" + " or ".join(p.describe() for p in self.of) + ")"


class Not(_Base):
    kind: Literal["not"] = Field(default="not", alias="assert")
    of: Predicate

    def evaluate(self, snapshot: UiSnapshot, inputs: Mapping[str, object]) -> bool:
        return not self.of.evaluate(snapshot, inputs)

    def describe(self) -> str:
        return f"not {self.of.describe()}"


Predicate: TypeAlias = Annotated[
    NodeExists
    | NodeAbsent
    | NodeHasValue
    | TextPresent
    | TextAbsent
    | UrlMatches
    | HttpStatusIn
    | AllOf
    | AnyOf
    | Not,
    Field(discriminator="kind"),
]
"""The closed set. A condition that cannot be expressed here has not been classified yet -- which is
the point: adding a new observable condition should require deciding what it means."""


AllOf.model_rebuild()
AnyOf.model_rebuild()
Not.model_rebuild()


def _resolve(value: ValueExpr, inputs: Mapping[str, object]) -> object | None:
    """Resolve an input or output reference against the run's bindings.

    Secrets are deliberately NOT resolvable here: a predicate must never be able to compare against
    a credential, because a failure message would then quote it.
    """
    if isinstance(value, InputRef | OutputRef):
        key = value.input_name if isinstance(value, InputRef) else value.output_name
        return inputs.get(key)
    return value
