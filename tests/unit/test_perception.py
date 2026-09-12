"""Normalization and snapshot building, without a browser.

These are pure functions, so they are tested as pure functions. The browser-backed behaviour lives
in tests/integration/test_driver_perception.py.
"""

from __future__ import annotations

from cua.perception.build import build_snapshot
from cua.perception.normalize import (
    fingerprint,
    fold,
    infer_region,
    is_noise,
    make_node_id,
    normalize_name,
    normalize_role,
)
from cua.perception.raw import RawElement


def test_browser_and_desktop_roles_land_in_one_vocabulary() -> None:
    """The portability claim, at the level it is actually implemented.

    A browser says `LayoutTableCell`; UIA says `DataItem`. If either leaked upward, an artifact
    would silently encode which surface it was recorded against.
    """
    assert normalize_role("LayoutTableCell") == "cell"
    assert normalize_role("DataItem") == "cell"
    assert normalize_role("textbox") == "textbox"
    assert normalize_role("Edit") == "textbox"
    assert normalize_role("Hyperlink") == normalize_role("link") == "link"


def test_noise_is_dropped() -> None:
    """A frameset app produces hundreds of these, and a snapshot becomes an LLM prompt during
    discovery -- they cost tokens and bury the controls that matter."""
    assert is_noise("LineBreak")
    assert is_noise("InlineTextBox")
    assert not is_noise("textbox")


def test_name_normalization_cleans_without_reinterpreting() -> None:
    assert normalize_name("Member\xa0Number:") == "Member Number"
    assert normalize_name("  Savings   Balance  ") == "Savings Balance"
    assert normalize_name("") is None


def test_normalization_does_not_absorb_rebranding() -> None:
    """The single most important property in this module.

    Folding must tolerate cosmetic differences and nothing more. If "Member #" folded to the same
    value as "Member Number", a rebranded tenant would resolve to a control the artifact never
    meant -- silently, and with no drift signal.
    """
    assert fold("Member  Number") == fold("member number") == "membernumber"
    assert fold("Member #") != fold("Member Number")


def test_node_ids_are_structural_and_stable() -> None:
    path = [("table", 0), ("row", 2), ("cell", 1), ("textbox", 0)]
    assert make_node_id("content", path) == "content:/table[0]/row[2]/cell[1]/textbox[0]"
    assert make_node_id("content", path) == make_node_id("content", path)
    assert make_node_id("nav", path) != make_node_id("content", path)


def test_fingerprint_notices_structural_moves() -> None:
    """A button keeping its name but moving from a toolbar into a dialog is a real change. A
    signature that ignored structure would call it identical."""
    in_table = fingerprint("button", "Search", ["document", "table", "row"], "content")
    in_dialog = fingerprint("button", "Search", ["document", "dialog"], "content")
    assert in_table != in_dialog
    assert in_table == fingerprint("button", "Search", ["document", "table", "row"], "content")


def test_region_inference_is_best_effort() -> None:
    """A region disambiguates; it is never identity. Failing to infer one costs a narrowing signal,
    never a wrong resolution."""
    assert infer_region("Member Search") == "search_form"
    assert infer_region("Find a Member") == "search_form"
    assert infer_region("Unrelated Heading") is None
    assert infer_region(None) is None


def _search_form_elements() -> list[RawElement]:
    """The shape the mock app actually produces: label in one cell, control in the next."""
    return [
        RawElement("1", "RootWebArea", "MemberDesk", child_ids=["2"], frame="content"),
        RawElement("2", "LayoutTable", child_ids=["3", "4"], parent_id="1", frame="content"),
        RawElement("3", "LayoutTableRow", child_ids=["5"], parent_id="2", frame="content"),
        RawElement("5", "LayoutTableCell", "Member Search", parent_id="3", frame="content"),
        RawElement("4", "LayoutTableRow", child_ids=["6", "7"], parent_id="2", frame="content"),
        RawElement("6", "LayoutTableCell", "Member Number:", parent_id="4", frame="content"),
        RawElement("7", "LayoutTableCell", child_ids=["8"], parent_id="4", frame="content"),
        RawElement(
            "8",
            "textbox",
            "Member Number",
            parent_id="7",
            frame="content",
            attributes={"name": "memno", "class": "frmfld"},
            native_handle=99,
        ),
        RawElement("9", "LineBreak", parent_id="2", frame="content"),
    ]


def test_build_produces_a_navigable_tree() -> None:
    snapshot = build_snapshot(_search_form_elements(), snapshot_id="s1", url="http://x/search")

    textbox = next(n for n in snapshot.nodes if n.role == "textbox")
    row = snapshot.nearest_ancestor(textbox, "row")
    assert row is not None

    cells = [n.name for n in snapshot.descendants(row) if n.role == "cell"]
    assert cells[0] == "Member Number", "structural anchoring needs the label cell reachable"


def test_build_drops_noise_and_keeps_everything_else() -> None:
    elements = _search_form_elements()
    snapshot = build_snapshot(elements, snapshot_id="s1", url="u")
    assert len(snapshot.nodes) == len(elements) - 1  # the LineBreak


def test_build_is_deterministic() -> None:
    elements = _search_form_elements()
    first = build_snapshot(elements, snapshot_id="a", url="u")
    second = build_snapshot(elements, snapshot_id="b", url="u")
    assert [n.node_id for n in first.nodes] == [n.node_id for n in second.nodes]
    assert [n.fingerprint for n in first.nodes] == [n.fingerprint for n in second.nodes]


def test_hints_are_attached_but_are_not_identity() -> None:
    """A hint is the brittle selector a scraper would cache. Keeping it as a fast path -- rather
    than as the control's identity -- is what survives a tenant's restyle."""
    from cua.surfaces.playwright_cdp.ax import css_hint

    snapshot = build_snapshot(
        _search_form_elements(), snapshot_id="s", url="u", hint_builder=css_hint
    )
    textbox = next(n for n in snapshot.nodes if n.role == "textbox")
    assert textbox.hints["css"] == 'input[name="memno"]'
    assert textbox.name == "Member Number", "the semantic handle is what identifies it"
