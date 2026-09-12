"""The resolution ladder's ordering and drift semantics.

The resolver itself arrives in Phase 04; this covers the ordering contract it must implement, which
lives in the domain because *which rung resolved a target* is part of the artifact's contract.
"""

from __future__ import annotations

from cua.domain.target import (
    LADDER,
    REPLAY_FORBIDDEN,
    Anchor,
    AnchorRelation,
    MatchMode,
    NameMatch,
    ResolutionStrategy,
    TargetDescriptor,
    is_drift,
    ladder_rank,
)


def test_ladder_is_ordered_strongest_first() -> None:
    """Fixed order is what makes resolution deterministic. A ladder that reorders itself -- by
    score, by recency, by dict iteration -- would make two replays disagree."""
    assert LADDER[0] is ResolutionStrategy.SEMANTIC_EXACT
    assert LADDER[-1] is ResolutionStrategy.VISION
    assert len(LADDER) == len(set(LADDER)) == len(ResolutionStrategy)
    assert ladder_rank(ResolutionStrategy.SEMANTIC_EXACT) < ladder_rank(
        ResolutionStrategy.STRUCTURAL_ANCHOR
    )


def test_vision_is_forbidden_in_replay() -> None:
    """Pixel coordinates and OCR are not reproducible across viewport size, zoom, font settings or
    scroll position. Allowing them in replay would quietly break the determinism guarantee, so
    vision stays a discovery aid."""
    assert {ResolutionStrategy.VISION} == REPLAY_FORBIDDEN


def test_drift_is_descent_not_mere_difference() -> None:
    """Drift means the surface moved *against* the artifact. Resolving more strongly than recorded
    is good news, not a warning -- reporting it would train people to ignore drift."""
    assert is_drift(ResolutionStrategy.SEMANTIC_EXACT, ResolutionStrategy.STRUCTURAL_ANCHOR)
    assert not is_drift(ResolutionStrategy.STRUCTURAL_ANCHOR, ResolutionStrategy.SEMANTIC_EXACT)
    assert not is_drift(ResolutionStrategy.SEMANTIC_EXACT, ResolutionStrategy.SEMANTIC_EXACT)
    assert not is_drift(None, ResolutionStrategy.VISION)


def test_descriptor_describes_itself_for_failure_messages() -> None:
    """A reviewer reading TARGET_NOT_FOUND should know what was being looked for without opening
    the artifact."""
    target = TargetDescriptor(
        role="textbox",
        name=NameMatch(value="Member Number", match=MatchMode.EXACT),
        anchor=Anchor(relation=AnchorRelation.ROW_OF, text="Member Number"),
    )
    described = target.describe()
    assert "textbox" in described
    assert "Member Number" in described
    assert "row_of" in described


def test_role_is_the_one_required_signal() -> None:
    """Role is what every surface agrees on -- ARIA, UIA and macOS AX all have it. A descriptor
    without one could not be resolved on a non-web surface at all."""
    assert TargetDescriptor.model_fields["role"].is_required()
    assert not TargetDescriptor.model_fields["name"].is_required()
