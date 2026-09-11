"""The closed action space.

Closed is the operative word, and it buys three things:

**The model cannot invent an action.** During discovery the LLM emits tool calls drawn from exactly
this set. There is no "execute this script" escape hatch, so the blast radius of a confused or
prompt-injected model is bounded by what is enumerated here.

**Policy can reason exhaustively.** Every member has a declared risk tier and a declared disposition
per actor. There is no "other" case for the allowlist to fall through.

**A human's input is an action, not a bypass.** `RawInput` exists so that when an operator takes
over a live session, their clicks and keystrokes travel the same
`Action -> PolicyEngine -> TargetResolver -> SurfaceDriver` path as everything else -- policed,
classified, and recorded. Without it the natural implementation is to inject CDP events straight
into the page, which silently disables every guardrail at exactly the moment a regulated system
most needs an audit trail. See ADR 0004.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from cua.domain.predicates import Predicate
from cua.domain.target import TargetDescriptor
from cua.domain.values import ValueExpr


class ActionRisk(StrEnum):
    """How much damage this action could do if it is wrong.

    Classified from three independent signals -- the action type, the target control's semantics
    (a configurable danger lexicon matched against its accessible name), and the step's explicit
    annotation -- because any one of them alone can be fooled. The highest tier wins.
    """

    SAFE = "safe"
    """Reversible: reading, navigating, typing into a field without submitting."""

    ELEVATED = "elevated"
    """State-changing but recoverable: submitting a search, saving a draft, opening a request."""

    IRREVERSIBLE = "irreversible"
    """Money movement, deletion, posting a transaction. Automation never does these unattended:
    blocked during discovery, and gated on an approved capability plus explicit caller opt-in
    during replay."""


class ScrollDirection(StrEnum):
    UP = "up"
    DOWN = "down"
    INTO_VIEW = "into_view"


class RawInputKind(StrEnum):
    """Human console input. Deliberately coarse.

    A mouse-move is not a semantically meaningful unit of authorization, and pretending otherwise
    would be theatre. Policy evaluates these on coarse attributes -- the scope they land in, and any
    navigation that results -- and this limitation is stated in REPORT.md §6 rather than hidden.
    """

    MOUSE_CLICK = "mouse_click"
    MOUSE_MOVE = "mouse_move"
    KEY = "key"
    TEXT = "text"


class _Action(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    risk: ActionRisk | None = None
    """Explicit annotation. One of the three risk signals; never the only one consulted."""


class Click(_Action):
    type: Literal["click"] = "click"
    target: TargetDescriptor


class Type(_Action):
    type: Literal["type"] = "type"
    target: TargetDescriptor
    value: ValueExpr
    clear_first: bool = True
    """Legacy forms frequently retain prior input. Appending to a stale value is a silent data
    error, so clearing is the default rather than the option."""


class Select(_Action):
    type: Literal["select"] = "select"
    target: TargetDescriptor
    value: ValueExpr


class PressKey(_Action):
    type: Literal["press_key"] = "press_key"
    key: str
    target: TargetDescriptor | None = None


class Navigate(_Action):
    type: Literal["navigate"] = "navigate"
    url: str
    """May contain `{base_url}` and `{input_name}` placeholders, resolved per tenant and per call.
    Always checked against the allowlist before dispatch."""


class Reload(_Action):
    """Re-fetch the current page.

    Distinct from `Navigate`: a recovery rule for a transient 502 must retry wherever the run
    happens to be, and replay does not know that URL statically.
    """

    type: Literal["reload"] = "reload"


class Scroll(_Action):
    type: Literal["scroll"] = "scroll"
    direction: ScrollDirection = ScrollDirection.INTO_VIEW
    target: TargetDescriptor | None = None
    amount: int | None = None


class WaitFor(_Action):
    type: Literal["wait_for"] = "wait_for"
    until: Predicate
    timeout_ms: int = 5000
    """Wait on a *condition*, never on a duration. A sleep in replay is a determinism bug: it
    passes on a fast machine and fails on a slow one, which is the worst kind of flake."""


class Extract(_Action):
    type: Literal["extract"] = "extract"
    target: TargetDescriptor
    into: str
    """Name of the declared output this populates."""
    transform: str | None = None
    """Optional named normalizer, e.g. `money` to turn "$4,210.55" into a typed amount."""


class Assert(_Action):
    type: Literal["assert"] = "assert"
    that: Predicate


class RawInput(_Action):
    """Human console input. `actor=HUMAN` only -- automation may never originate one."""

    type: Literal["raw_input"] = "raw_input"
    kind: RawInputKind
    x: float | None = None
    y: float | None = None
    key: str | None = None
    text: str | None = None
    frame: str | None = None


Action: TypeAlias = Annotated[
    Click
    | Type
    | Select
    | PressKey
    | Navigate
    | Reload
    | Scroll
    | WaitFor
    | Extract
    | Assert
    | RawInput,
    Field(discriminator="type"),
]

READ_ONLY_ACTIONS: frozenset[str] = frozenset({"wait_for", "assert", "extract", "scroll"})
"""Actions that cannot change application state, whatever they are pointed at."""

HUMAN_ONLY_ACTIONS: frozenset[str] = frozenset({"raw_input"})
"""Actions automation may never originate."""


def target_of(action: Action) -> TargetDescriptor | None:
    """The control this action acts on, if any. Used by the risk classifier and the resolver."""
    return getattr(action, "target", None)
