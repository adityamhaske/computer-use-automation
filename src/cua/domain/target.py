"""How a capability identifies a control.

This is the type that decides whether an artifact is portable or brittle. It describes a control the
way a human operator would -- *the Search button*, *the field in the row labelled Member Number* --
rather than the way a scraper would.

The rule the whole design rests on: **a `TargetDescriptor` never carries a CSS selector as its
identity.** Surface-specific data lives only in `hints`, which is an unverified cache. A hint may be
tried as a fast path, but the node it finds is accepted only if it *also* satisfies the semantic
assertion. That is what stops the artifact from quietly becoming CSS-coupled, and it is what lets
the same artifact resolve against a Windows UIA tree where no CSS exists.

See docs/design/target-resolution.md for the ladder and its determinism rules.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from cua.domain.snapshot import NodeScope


class MatchMode(StrEnum):
    """How strictly a name must match."""

    EXACT = "exact"
    NORMALIZED = "normalized"
    """Case, whitespace and punctuation folded. Absorbs cosmetic edits -- NOT rebranding:
    "Member #" is not a normalization of "Member Number", and treating it as one would be guessing.
    """
    CONTAINS = "contains"
    REGEX = "regex"


class NameMatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str
    match: MatchMode = MatchMode.EXACT


class AnchorRelation(StrEnum):
    """How a control relates to the text that identifies it.

    Legacy enterprise screens put the label in an adjacent table cell rather than a `<label for>`,
    so the control itself frequently has no accessible name at all. These relations are how such a
    control is found -- and they are expressed structurally, not as a CSS path, so they mean the
    same thing on a desktop surface.
    """

    ROW_OF = "row_of"
    """The control inside the row whose label cell carries the anchor text."""

    ADJACENT_TO = "adjacent_to"
    """The control in the cell immediately following the one carrying the anchor text."""

    WITHIN_SECTION = "within_section"
    """The control inside the section or table introduced by the anchor heading."""


class Anchor(BaseModel):
    """A structural relationship to nearby identifying text.

    Note the limitation, stated rather than hidden: the anchor *is* the label text. A tenant that
    rebrands "Savings Balance" to "Savings Bal." breaks the anchor too. That case is handled by a
    `TenantBinding` overlay, not by fuzzy matching -- see ADR 0005.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    relation: AnchorRelation
    text: str
    match: MatchMode = MatchMode.NORMALIZED


class ResolutionStrategy(StrEnum):
    """The rungs of the resolution ladder, in priority order.

    Declared here, next to the schema, because which strategy resolved a target is part of the
    artifact's contract: it is the baseline against which drift is measured.
    """

    SEMANTIC_EXACT = "semantic_exact"
    SEMANTIC_NORMALIZED = "semantic_normalized"
    LABEL_ASSOCIATION = "label_association"
    STRUCTURAL_ANCHOR = "structural_anchor"
    HINT_CACHED = "hint_cached"
    ORDINAL_IN_REGION = "ordinal_in_region"
    VISION = "vision"


LADDER: tuple[ResolutionStrategy, ...] = (
    ResolutionStrategy.SEMANTIC_EXACT,
    ResolutionStrategy.SEMANTIC_NORMALIZED,
    ResolutionStrategy.LABEL_ASSOCIATION,
    ResolutionStrategy.STRUCTURAL_ANCHOR,
    ResolutionStrategy.HINT_CACHED,
    ResolutionStrategy.ORDINAL_IN_REGION,
    ResolutionStrategy.VISION,
)
"""The fixed order strategies are attempted in. Fixed = deterministic."""

REPLAY_FORBIDDEN: frozenset[ResolutionStrategy] = frozenset({ResolutionStrategy.VISION})
"""Strategies unavailable during replay.

Vision is a discovery aid. Pixel coordinates and OCR are not reproducible across viewport sizes,
font settings or scroll position, so allowing them in replay would quietly break the determinism
guarantee. Enforced by test, not merely by configuration.
"""


def ladder_rank(strategy: ResolutionStrategy) -> int:
    """Position in the ladder. Lower is stronger evidence of a confident match."""
    return LADDER.index(strategy)


def is_drift(recorded: ResolutionStrategy | None, used: ResolutionStrategy) -> bool:
    """True if resolution fell to a weaker rung than the one recorded at discovery.

    Not an error -- a signal. It means the surface has moved under the artifact, and it is visible
    *before* replays start failing, which is the window in which a re-record or a tenant overlay is
    still cheap.
    """
    return recorded is not None and ladder_rank(used) > ladder_rank(recorded)


class TargetDescriptor(BaseModel):
    """A semantic description of a control, resolved against a `UiSnapshot`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str
    """Required. The one signal every surface agrees on."""

    name: NameMatch | None = None
    scope: NodeScope = Field(default_factory=NodeScope)
    anchor: Anchor | None = None

    ordinal: int | None = None
    """Zero-based index among equally-matching candidates within `scope`.

    The weakest rung, and deliberately so: it survives rebranding but not reordering. Present as a
    last resort before refusal.
    """

    hints: dict[str, str] = Field(default_factory=dict)
    """Driver-specific fast paths (css, node_path). A cache, never an identity -- a hint is accepted
    only when the node it resolves to also satisfies the semantic assertion above."""

    fingerprint: str = ""
    """Structural signature captured at discovery, compared at replay to detect drift."""

    recorded_strategy: ResolutionStrategy | None = None
    """Which rung won at discovery. The drift baseline."""

    def describe(self) -> str:
        """A human-readable one-liner, for failure messages and review.

        A reviewer reading `TARGET_NOT_FOUND` needs to know what was being looked for without
        opening the artifact.
        """
        parts = [self.role]
        if self.name:
            parts.append(f'named {self.name.match.value} "{self.name.value}"')
        if self.anchor:
            parts.append(f'{self.anchor.relation.value} "{self.anchor.text}"')
        if self.scope.frame:
            parts.append(f"in frame {self.scope.frame}")
        if self.ordinal is not None:
            parts.append(f"#{self.ordinal}")
        return " ".join(parts)
