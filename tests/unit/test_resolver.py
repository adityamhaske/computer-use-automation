"""The resolution ladder.

Determinism and refusal are the two properties that matter, and both are asserted directly rather
than inferred from the resolver happening to work.
"""

from __future__ import annotations

import pytest

from cua.domain.snapshot import NodeScope, UiSnapshot
from cua.domain.target import (
    Anchor,
    AnchorRelation,
    MatchMode,
    NameMatch,
    ResolutionStrategy,
    TargetDescriptor,
)
from cua.perception.build import build_snapshot
from cua.perception.raw import RawElement
from cua.targeting.resolver import TargetResolutionError, TargetResolver


def _search_page() -> UiSnapshot:
    """The mock app's search form: label cell, then a cell holding the control.

    The textbox carries an accessible name (from a `title`, as legacy apps often do) while the
    submit button's name comes from its `value`. Both rungs therefore have something to work with,
    which is what the real page looks like.
    """
    elements = [
        RawElement("1", "RootWebArea", "MemberDesk", child_ids=["2"], frame="content"),
        RawElement(
            "2", "LayoutTable", child_ids=["r1", "r2", "r3"], parent_id="1", frame="content"
        ),
        RawElement("r1", "LayoutTableRow", child_ids=["c1"], parent_id="2", frame="content"),
        RawElement("c1", "LayoutTableCell", "Member Search", parent_id="r1", frame="content"),
        RawElement("r2", "LayoutTableRow", child_ids=["c2", "c3"], parent_id="2", frame="content"),
        RawElement("c2", "LayoutTableCell", "Member Number", parent_id="r2", frame="content"),
        RawElement("c3", "LayoutTableCell", child_ids=["tb"], parent_id="r2", frame="content"),
        RawElement(
            "tb",
            "textbox",
            "Member Number",
            parent_id="c3",
            frame="content",
            attributes={"name": "memno"},
        ),
        RawElement("r3", "LayoutTableRow", child_ids=["c4"], parent_id="2", frame="content"),
        RawElement("c4", "LayoutTableCell", child_ids=["btn"], parent_id="r3", frame="content"),
        RawElement("btn", "button", "Search", parent_id="c4", frame="content"),
    ]
    from cua.surfaces.playwright_cdp.ax import css_hint

    return build_snapshot(elements, snapshot_id="s", url="http://x/search", hint_builder=css_hint)


def _detail_page() -> UiSnapshot:
    """The member record: a value stated as the cell adjacent to its label."""
    elements = [
        RawElement("1", "RootWebArea", "MemberDesk", child_ids=["t"], frame="content"),
        RawElement("t", "LayoutTable", child_ids=["r1", "r2"], parent_id="1", frame="content"),
        RawElement("r1", "LayoutTableRow", child_ids=["a", "b"], parent_id="t", frame="content"),
        RawElement("a", "LayoutTableCell", "Savings Balance", parent_id="r1", frame="content"),
        RawElement("b", "LayoutTableCell", "$4,210.55", parent_id="r1", frame="content"),
        RawElement("r2", "LayoutTableRow", child_ids=["c", "d"], parent_id="t", frame="content"),
        RawElement("c", "LayoutTableCell", "Status", parent_id="r2", frame="content"),
        RawElement("d", "LayoutTableCell", "Active", parent_id="r2", frame="content"),
    ]
    return build_snapshot(elements, snapshot_id="s", url="http://x/member/12345")


@pytest.fixture
def resolver() -> TargetResolver:
    return TargetResolver(allow_vision=False)


# --------------------------------------------------------------- happy path


def test_semantic_exact_is_preferred(resolver: TargetResolver) -> None:
    target = TargetDescriptor(role="textbox", name=NameMatch(value="Member Number"))
    resolution = resolver.resolve(target, _search_page())

    assert resolution.strategy is ResolutionStrategy.SEMANTIC_EXACT
    assert resolution.node.role == "textbox"
    assert resolution.ambiguity == 0.0


def test_normalized_matching_absorbs_cosmetic_differences(resolver: TargetResolver) -> None:
    """A label that gained a colon or extra spacing is the same control."""
    target = TargetDescriptor(
        role="textbox", name=NameMatch(value="  member   number: ", match=MatchMode.EXACT)
    )
    resolution = resolver.resolve(target, _search_page())
    assert resolution.strategy is ResolutionStrategy.SEMANTIC_NORMALIZED


def test_normalized_matching_does_not_absorb_rebranding(resolver: TargetResolver) -> None:
    """The single most important negative result in the resolver.

    "Member #" is not a normalization of "Member Number". A rung that closed that gap would resolve
    a rebranded tenant's control to something the artifact never meant -- silently, with no drift
    signal. That case is for a TenantBinding overlay, not for a cleverer matcher.
    """
    target = TargetDescriptor(role="textbox", name=NameMatch(value="Member #"))
    with pytest.raises(TargetResolutionError) as raised:
        resolver.resolve(target, _search_page())
    assert "no candidate matched" in raised.value.reason


# ------------------------------------------------------------- refusal


def _two_identical_buttons() -> UiSnapshot:
    elements = [
        RawElement("1", "RootWebArea", "x", child_ids=["a", "b"], frame="content"),
        RawElement("a", "button", "Submit", parent_id="1", frame="content"),
        RawElement("b", "button", "Submit", parent_id="1", frame="content"),
    ]
    return build_snapshot(elements, snapshot_id="s", url="http://x")


def test_ambiguity_is_refused_not_tiebroken(resolver: TargetResolver) -> None:
    """The property this design is willing to pay for.

    Two plausible Submit buttons means the page is not what the artifact thinks it is. A refusal
    escalates to a human and costs minutes; a wrong click on a financial screen may not be
    recoverable.
    """
    target = TargetDescriptor(role="button", name=NameMatch(value="Submit"))
    with pytest.raises(TargetResolutionError) as raised:
        resolver.resolve(target, _two_identical_buttons())

    failure = raised.value
    assert failure.is_ambiguous
    assert len(failure.candidates) == 2
    assert failure.ambiguity == 0.5


def test_refusal_carries_debugging_context(resolver: TargetResolver) -> None:
    """Brief §3.3: a failure must be debuggable without reproducing it."""
    target = TargetDescriptor(role="button", name=NameMatch(value="Submit"))
    with pytest.raises(TargetResolutionError) as raised:
        resolver.resolve(target, _two_identical_buttons())

    failure = raised.value
    assert failure.target.describe()
    assert ResolutionStrategy.SEMANTIC_EXACT in failure.strategies_tried
    assert len(failure.candidate_summaries()) == 2
    assert all("button" in summary for summary in failure.candidate_summaries())


def test_an_explicit_ordinal_disambiguates(resolver: TargetResolver) -> None:
    """An ordinal the author supplied on purpose is a deterministic choice, not a guess."""
    target = TargetDescriptor(role="button", name=NameMatch(value="Submit"), ordinal=1)
    resolution = resolver.resolve(target, _two_identical_buttons())
    assert resolution.node.node_id.endswith("button[1]")


def test_scope_narrows_across_frames() -> None:
    """The same name commonly appears in the nav frame and the content frame on a frameset app.
    Resolving to the wrong one is a wrong click, not a failure."""
    elements = [
        RawElement("n1", "RootWebArea", "nav", child_ids=["n2"], frame="nav"),
        RawElement("n2", "link", "Member Search", parent_id="n1", frame="nav"),
        RawElement("c1", "RootWebArea", "content", child_ids=["c2"], frame="content"),
        RawElement("c2", "link", "Member Search", parent_id="c1", frame="content"),
    ]
    snapshot = build_snapshot(elements, snapshot_id="s", url="http://x")
    resolver = TargetResolver()

    unscoped = TargetDescriptor(role="link", name=NameMatch(value="Member Search"))
    with pytest.raises(TargetResolutionError):
        resolver.resolve(unscoped, snapshot)

    scoped = TargetDescriptor(
        role="link", name=NameMatch(value="Member Search"), scope=NodeScope(frame="nav")
    )
    assert resolver.resolve(scoped, snapshot).node.scope.frame == "nav"


# -------------------------------------------------------- structural anchor


def test_structural_anchor_finds_a_control_with_no_name(resolver: TargetResolver) -> None:
    """The rung that makes legacy screens tractable.

    The control carries no accessible name at all; the only handle is the label cell beside it,
    exactly as a human reads the row.
    """
    elements = [
        RawElement("1", "RootWebArea", "x", child_ids=["t"], frame="content"),
        RawElement("t", "LayoutTable", child_ids=["r"], parent_id="1", frame="content"),
        RawElement(
            "r", "LayoutTableRow", child_ids=["lbl", "cell"], parent_id="t", frame="content"
        ),
        RawElement("lbl", "LayoutTableCell", "Account Type", parent_id="r", frame="content"),
        RawElement("cell", "LayoutTableCell", child_ids=["sel"], parent_id="r", frame="content"),
        RawElement("sel", "combobox", None, parent_id="cell", frame="content"),
    ]
    snapshot = build_snapshot(elements, snapshot_id="s", url="http://x")

    target = TargetDescriptor(
        role="combobox", anchor=Anchor(relation=AnchorRelation.ROW_OF, text="Account Type")
    )
    resolution = resolver.resolve(target, snapshot)

    assert resolution.strategy is ResolutionStrategy.STRUCTURAL_ANCHOR
    assert resolution.node.role == "combobox"
    assert resolution.node.name is None


def test_adjacent_to_extracts_the_value_beside_its_label(resolver: TargetResolver) -> None:
    """How a legacy screen states a fact: label left, value right, no markup linking them."""
    target = TargetDescriptor(
        role="cell", anchor=Anchor(relation=AnchorRelation.ADJACENT_TO, text="Savings Balance")
    )
    resolution = resolver.resolve(target, _detail_page())

    assert resolution.strategy is ResolutionStrategy.STRUCTURAL_ANCHOR
    assert resolution.node.name == "$4,210.55"


def test_adjacent_to_means_the_next_cell_not_every_later_cell(resolver: TargetResolver) -> None:
    """Regression. In a multi-column grid, "the cell after Savings" must be exactly one cell.

    An earlier version returned every following sibling, so a four-column account row offered three
    candidates and the resolver -- correctly refusing ambiguity -- failed every extraction from a
    table wider than two columns. Caught because discovery verifies each synthesized descriptor by
    resolving it back.
    """
    elements = [
        RawElement("1", "RootWebArea", "x", child_ids=["t"], frame="content"),
        RawElement("t", "LayoutTable", child_ids=["r"], parent_id="1", frame="content"),
        RawElement(
            "r", "LayoutTableRow", child_ids=["a", "b", "c", "d"], parent_id="t", frame="content"
        ),
        RawElement("a", "LayoutTableCell", "0001234501", parent_id="r", frame="content"),
        RawElement("b", "LayoutTableCell", "Savings", parent_id="r", frame="content"),
        RawElement("c", "LayoutTableCell", "$4,210.55", parent_id="r", frame="content"),
        RawElement("d", "LayoutTableCell", "Active", parent_id="r", frame="content"),
    ]
    snapshot = build_snapshot(elements, snapshot_id="s", url="http://x")

    target = TargetDescriptor(
        role="cell", anchor=Anchor(relation=AnchorRelation.ADJACENT_TO, text="Savings")
    )
    assert resolver.resolve(target, snapshot).node.name == "$4,210.55"


def test_an_anchor_is_never_its_own_candidate(resolver: TargetResolver) -> None:
    """Regression. The label cell was being offered as a candidate for its own anchor, because the
    exclusion compared object identity rather than node id -- which made every two-cell lookup come
    back ambiguous."""
    target = TargetDescriptor(
        role="cell", anchor=Anchor(relation=AnchorRelation.ROW_OF, text="Savings Balance")
    )
    resolved = resolver.resolve(target, _detail_page())
    assert resolved.node.name == "$4,210.55"


def test_adjacent_to_does_not_leak_across_rows(resolver: TargetResolver) -> None:
    """Scoped to the anchor's own row -- otherwise 'the cell after Status' could pick up a value
    from the row below, which is exactly the class of silent error this design exists to prevent."""
    target = TargetDescriptor(
        role="cell", anchor=Anchor(relation=AnchorRelation.ADJACENT_TO, text="Status")
    )
    assert resolver.resolve(target, _detail_page()).node.name == "Active"


# ----------------------------------------------------------- cached hints


def test_a_hint_is_verified_not_trusted(resolver: TargetResolver) -> None:
    """The rule that stops an artifact from silently becoming CSS-coupled.

    Here the cached selector still matches a node on the page, but that node is the wrong role. The
    hint is discarded rather than clicked.
    """
    elements = [
        RawElement("1", "RootWebArea", "x", child_ids=["a"], frame="content"),
        RawElement(
            "a",
            "button",
            "Not the field",
            parent_id="1",
            frame="content",
            attributes={"name": "memno"},
        ),
    ]
    from cua.surfaces.playwright_cdp.ax import css_hint

    snapshot = build_snapshot(elements, snapshot_id="s", url="http://x", hint_builder=css_hint)

    target = TargetDescriptor(role="textbox", hints={"css": 'input[name="memno"]'})
    with pytest.raises(TargetResolutionError):
        resolver.resolve(target, snapshot)


# ------------------------------------------------------------------ drift


def test_descending_the_ladder_is_drift(resolver: TargetResolver) -> None:
    """A tenant restyled the page: the cached selector is dead and the name survived, so resolution
    falls from `hint_cached`... or rather, is recorded as having been found more weakly than before.

    Drift is the early warning -- visible before replays begin to fail, which is the window in which
    a re-record or an overlay is still cheap.
    """
    target = TargetDescriptor(
        role="cell",
        anchor=Anchor(relation=AnchorRelation.ADJACENT_TO, text="Savings Balance"),
        recorded_strategy=ResolutionStrategy.SEMANTIC_EXACT,
    )
    resolution, drifted = resolver.resolve_with_drift(target, _detail_page())

    assert resolution.strategy is ResolutionStrategy.STRUCTURAL_ANCHOR
    assert drifted, "resolving more weakly than recorded is drift"


def test_resolving_as_recorded_is_not_drift(resolver: TargetResolver) -> None:
    target = TargetDescriptor(
        role="textbox",
        name=NameMatch(value="Member Number"),
        recorded_strategy=ResolutionStrategy.SEMANTIC_EXACT,
    )
    _, drifted = resolver.resolve_with_drift(target, _search_page())
    assert not drifted


# ------------------------------------------------------------ determinism


def test_repeated_resolution_is_identical(resolver: TargetResolver) -> None:
    """Same artifact, same page, same answer -- every time. If two replays can disagree, a
    divergence is unattributable: it could be a UI change or a coin flip."""
    snapshot = _search_page()
    target = TargetDescriptor(role="textbox", name=NameMatch(value="Member Number"))

    results = [resolver.resolve(target, snapshot) for _ in range(25)]
    assert len({r.node.node_id for r in results}) == 1
    assert len({r.strategy for r in results}) == 1


def test_vision_is_unreachable_during_replay() -> None:
    """Pixel coordinates are not reproducible across viewport size, zoom or scroll position.
    Allowing them in replay would quietly void the determinism guarantee."""
    assert ResolutionStrategy.VISION not in TargetResolver(allow_vision=False).ladder()
    assert ResolutionStrategy.VISION in TargetResolver(allow_vision=True).ladder()


def test_resolver_config_is_fingerprinted() -> None:
    """Recorded into every run so that "the same artifact produced a different trace" is
    attributable: a UI change and a resolver change demand opposite responses."""
    replay = TargetResolver(allow_vision=False)
    discovery = TargetResolver(allow_vision=True)
    assert replay.config_fingerprint() != discovery.config_fingerprint()
    assert replay.config_fingerprint() == TargetResolver(allow_vision=False).config_fingerprint()
