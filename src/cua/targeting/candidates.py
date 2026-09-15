"""Candidate generation for each rung of the resolution ladder.

Every function here is **pure**: `(descriptor, snapshot) -> candidates in document order`. No clock,
no randomness, no reliance on set or dict iteration order. That is not stylistic — it is the whole
basis of the determinism guarantee. A scorer that consulted anything else would make two replays of
the same artifact against the same page disagree, and there would be no way to tell that from a real
UI change.

Document order is the tie-break of last resort, so candidate lists are always built by walking
`snapshot.nodes` in order rather than by filtering a set.
"""

from __future__ import annotations

from cua.domain.snapshot import UiNode, UiSnapshot
from cua.domain.target import AnchorRelation, MatchMode, TargetDescriptor
from cua.perception.normalize import fold, normalize_name


def _in_scope(node: UiNode, target: TargetDescriptor) -> bool:
    """Scope narrows; it never identifies.

    Frame scoping matters more than it looks on a frameset app: the same accessible name commonly
    appears in the navigation frame and the content frame, and resolving to the wrong one is a
    wrong click rather than a failure.
    """
    if target.scope.frame is not None and node.scope.frame != target.scope.frame:
        return False
    return not (target.scope.region is not None and node.scope.region != target.scope.region)


def _role_matches(node: UiNode, target: TargetDescriptor) -> bool:
    return node.role == target.role


def _base_pool(snapshot: UiSnapshot, target: TargetDescriptor) -> list[UiNode]:
    return [n for n in snapshot.nodes if _role_matches(n, target) and _in_scope(n, target)]


def ordinal_pool(snapshot: UiSnapshot, target: TargetDescriptor) -> list[UiNode]:
    """The candidate list an `ordinal` indexes into, in document order.

    Public because the *recorder* has to count over exactly this list. It did not: it enumerated
    role + frame while the resolver filters role + frame + region, so a descriptor carrying a region
    recorded a position counted across the whole frame and replayed it against a narrower list. That
    resolves -- to the wrong control, silently, which is worse than failing. Sharing the function is
    what makes the two definitionally the same rather than the same by inspection.
    """
    return _base_pool(snapshot, target)


def semantic_exact(target: TargetDescriptor, snapshot: UiSnapshot) -> list[UiNode]:
    """Role plus accessible name, matched exactly after whitespace cleanup."""
    if target.name is None:
        return []
    wanted = normalize_name(target.name.value)
    pool = _base_pool(snapshot, target)

    if target.name.match is MatchMode.EXACT:
        return [n for n in pool if normalize_name(n.name) == wanted]
    if target.name.match is MatchMode.CONTAINS:
        return [n for n in pool if wanted and wanted.lower() in (n.name or "").lower()]
    if target.name.match is MatchMode.REGEX:
        import re

        return [n for n in pool if n.name and re.search(target.name.value, n.name)]
    return []


def semantic_normalized(target: TargetDescriptor, snapshot: UiSnapshot) -> list[UiNode]:
    """Role plus name with case, spacing and punctuation folded.

    Absorbs cosmetic edits ("Member Number:" vs "Member  Number"). Deliberately does not absorb
    rebranding: "Member #" folds to "member", not "membernumber". A rung that closed that gap would
    be guessing, and a resolver that guesses eventually clicks the wrong control.
    """
    if target.name is None:
        return []
    wanted = fold(target.name.value)
    if not wanted:
        return []
    return [n for n in _base_pool(snapshot, target) if fold(n.name) == wanted]


def _anchor_matches(node: UiNode, text: str, mode: MatchMode) -> bool:
    if mode is MatchMode.EXACT:
        return normalize_name(node.name) == normalize_name(text)
    if mode is MatchMode.CONTAINS:
        return bool(node.name) and text.lower() in (node.name or "").lower()
    return fold(node.name) == fold(text)


def structural_anchor(target: TargetDescriptor, snapshot: UiSnapshot) -> list[UiNode]:
    """Find a control by the text that identifies it, rather than by anything on the control.

    This is the rung that makes legacy enterprise screens tractable. Those apps put the label in an
    adjacent table cell rather than in a `<label for>`, so the control frequently has no accessible
    name at all — a human reads the row, and so does this.

    Expressed as a structural relation rather than a path, so it means the same thing on a desktop
    surface: "the control in the row whose label cell says X" is as valid in UIA as in ARIA.
    """
    anchor = target.anchor
    if anchor is None:
        return []

    anchors = [n for n in snapshot.nodes if _anchor_matches(n, anchor.text, anchor.match)]
    found: list[UiNode] = []

    for anchor_node in anchors:
        if anchor.relation is AnchorRelation.ROW_OF:
            row = snapshot.nearest_ancestor(anchor_node, "row")
            if row is None:
                continue
            found.extend(
                n
                for n in snapshot.descendants(row)
                if _role_matches(n, target)
                and _in_scope(n, target)
                # By id, not identity: `is not` silently failed here, so the label cell was
                # offered as a candidate for its own anchor and every lookup came back ambiguous.
                and n.node_id != anchor_node.node_id
            )

        elif anchor.relation is AnchorRelation.ADJACENT_TO:
            # The value cell immediately following the label cell, in the same row. This is how a
            # legacy screen states a fact: label on the left, value on the right, no markup linking
            # them.
            parent = snapshot.parent(anchor_node)
            container = (
                parent
                if parent is not None and parent.role == "row"
                else snapshot.nearest_ancestor(anchor_node, "row")
            )
            if container is None:
                continue
            siblings = [
                n for n in snapshot.descendants(container) if snapshot.parent(n) is container
            ]
            # Walk to the anchor itself, or to the cell containing it, then take what follows.
            index = next(
                (
                    i
                    for i, sibling in enumerate(siblings)
                    if sibling.node_id == anchor_node.node_id
                    or anchor_node.node_id.startswith(sibling.node_id + "/")
                ),
                None,
            )
            if index is None:
                continue
            # The FIRST matching sibling after the anchor, not every one that follows.
            # "Adjacent to" means next to. Taking all of them made a four-column grid row offer
            # three candidates for "the cell after Savings", which the resolver then refused as
            # ambiguous -- so every extraction from a multi-column table failed to resolve.
            following = next(
                (
                    n
                    for n in siblings[index + 1 :]
                    if _role_matches(n, target) and _in_scope(n, target)
                ),
                None,
            )
            if following is not None:
                found.append(following)

        elif anchor.relation is AnchorRelation.WITHIN_SECTION:
            section = snapshot.nearest_ancestor(anchor_node, "table") or snapshot.parent(
                anchor_node
            )
            if section is None:
                continue
            found.extend(
                n
                for n in snapshot.descendants(section)
                if _role_matches(n, target)
                and _in_scope(n, target)
                and n.node_id != anchor_node.node_id
            )

    # De-duplicate while preserving document order -- two anchors can reach the same node.
    seen: set[str] = set()
    ordered: list[UiNode] = []
    for node in snapshot.nodes:
        if node.node_id in {n.node_id for n in found} and node.node_id not in seen:
            seen.add(node.node_id)
            ordered.append(node)
    return ordered


def hint_cached(target: TargetDescriptor, snapshot: UiSnapshot) -> list[UiNode]:
    """The stored surface-specific hint — a fast path, never an identity.

    A candidate found this way is returned only if it *also* satisfies the semantic assertion. That
    check is what stops an artifact from silently becoming CSS-coupled: when a tenant restyles the
    page, a stale hint that still happens to match something is discarded rather than clicked.
    """
    wanted = target.hints.get("css")
    if not wanted:
        return []

    matches = [n for n in snapshot.nodes if n.hints.get("css") == wanted]
    verified = [n for n in matches if _role_matches(n, target) and _in_scope(n, target)]

    if target.name is not None:
        expected = fold(target.name.value)
        verified = [n for n in verified if fold(n.name) == expected]
    return verified


def ordinal_in_region(target: TargetDescriptor, snapshot: UiSnapshot) -> list[UiNode]:
    """The Nth control of this role within the scope.

    The weakest rung, and last before refusal. It survives rebranding — which nothing above it does
    — but not reordering, so it is a position of last resort rather than a preference.
    """
    if target.ordinal is None:
        return []
    pool = _base_pool(snapshot, target)
    return [pool[target.ordinal]] if 0 <= target.ordinal < len(pool) else []
