"""Turning a concrete node into a semantic descriptor that finds it again.

This is the hinge between the two halves of the system. During discovery a model points at a node it
can see *right now*, by id. That id is worthless later -- it describes a position in one
observation.
What has to be recorded is a *description* that will find the same control on a future run, on a
different tenant's build of the same product.

The function is shared deliberately. The discovery loop uses it to act, and the Phase 07 compiler
uses it to record -- so what gets written into the artifact is exactly what was proven to resolve
during discovery, not a second guess at it. Two implementations here would be two chances to
disagree.

It also returns the rung that would resolve it, which becomes `recorded_strategy` in the artifact:
the baseline every later run's drift is measured against.
"""

from __future__ import annotations

from dataclasses import dataclass

from cua.domain.snapshot import NodeScope, UiNode, UiSnapshot
from cua.domain.target import (
    Anchor,
    AnchorRelation,
    MatchMode,
    NameMatch,
    ResolutionStrategy,
    TargetDescriptor,
)
from cua.perception.normalize import looks_like_value
from cua.targeting.candidates import ordinal_pool


@dataclass(frozen=True)
class Synthesis:
    descriptor: TargetDescriptor
    strategy: ResolutionStrategy
    unique: bool
    """False when nothing available made this node uniquely identifiable.

    Recorded rather than hidden: a step whose target could not be described unambiguously is one a
    human should look at before the capability is approved, and silently emitting an ordinal-based
    descriptor would bury that.
    """


READABLE_ROLES: frozenset[str] = frozenset({"cell", "text", "heading"})
"""Roles a capability *reads from* rather than acts on.

The distinction decides which structural relation describes them, and getting it wrong makes every
extraction ambiguous -- see `_anchor_for`.
"""


def _row_label(node: UiNode, snapshot: UiSnapshot) -> str | None:
    """The first named cell in the enclosing row -- what the row is *about*.

    Right for a control: a legacy form row holds one label and one input, so "the textbox in the row
    labelled Account Type" identifies it.
    """
    row = snapshot.nearest_ancestor(node, "row")
    if row is None:
        return None
    for candidate in snapshot.descendants(row):
        if candidate.node_id != node.node_id and candidate.role == "cell" and candidate.name:
            return candidate.name
    return None


def _preceding_label(node: UiNode, snapshot: UiSnapshot) -> str | None:
    """The named cell immediately before this one, among its siblings.

    Right for a value: a legacy screen states facts as label-then-value in adjacent cells, and in a
    multi-column grid the cell before is the only one that identifies this column's meaning.

    **A cell holding a datum is not a label.** This used to accept the first named sibling
    unconditionally -- correct on a label/value table, wrong on an n-column grid, where the cell
    before a status is a *balance*. The compiler then recorded `adjacent_to "$812.30"`, one member's
    checking balance, as though it were structure, and the capability replayed only for the member
    it was discovered on.

    Skipping datum-shaped siblings and continuing the scan is the fix. When nothing label-shaped
    remains we return None and let the caller fall down the ladder, because the real label in that
    layout is a column header this function cannot see. Refusing to describe a node beats describing
    it with data: the descriptor is then flagged `unique=False` and the compiler warns, instead of
    producing an artifact that looks right and is not.
    """
    parent = snapshot.parent(node)
    if parent is None:
        return None
    siblings = [n for n in snapshot.descendants(parent) if snapshot.parent(n) is parent]
    index = next((i for i, n in enumerate(siblings) if n.node_id == node.node_id), None)
    if index is None:
        return None
    for candidate in reversed(siblings[:index]):
        if candidate.name and not looks_like_value(candidate.name):
            return candidate.name
    return None


def _anchor_for(node: UiNode, snapshot: UiSnapshot) -> Anchor | None:
    """Describe this node by the text beside it, using the relation its role calls for.

    Reading a value uses ADJACENT_TO -- the single cell after the label. Using ROW_OF here would
    offer *every* cell in the row as a candidate, and since the resolver refuses ambiguity rather
    than guessing, every extraction would fail to resolve. That was a real bug, caught because
    synthesized descriptors are verified by resolving them back during discovery.

    Acting on a control uses ROW_OF: a form row holds one control of a given role, and the label may
    not be the immediately preceding sibling once a cell wraps it.
    """
    if node.role in READABLE_ROLES:
        label = _preceding_label(node, snapshot)
        relation = AnchorRelation.ADJACENT_TO
    else:
        label = _row_label(node, snapshot)
        relation = AnchorRelation.ROW_OF
    if not label:
        return None
    return Anchor(relation=relation, text=label, match=MatchMode.NORMALIZED)


def synthesize_descriptor(node: UiNode, snapshot: UiSnapshot) -> Synthesis:
    """Describe `node` the way a capability should, preferring the strongest rung that is unique.

    The order mirrors the resolution ladder, because a descriptor is only useful if the resolver
    would reach it the same way. Scope is always included: on a frameset app the same name commonly
    appears in the navigation frame and the content frame, and a descriptor that omitted the frame
    would be ambiguous the moment both are on screen.
    """
    scope = NodeScope(frame=node.scope.frame, region=node.scope.region)
    # Counted over the resolver's own pool, so a recorded ordinal means on replay exactly what it
    # meant at discovery. See `ordinal_pool`.
    in_scope = ordinal_pool(snapshot, TargetDescriptor(role=node.role, scope=scope))

    # 1. Role + accessible name, if that is unique.
    if node.name:
        same_name = [n for n in in_scope if n.name == node.name]
        if len(same_name) == 1:
            return Synthesis(
                TargetDescriptor(
                    role=node.role,
                    name=NameMatch(value=node.name, match=MatchMode.EXACT),
                    scope=scope,
                    anchor=_anchor_for(node, snapshot),
                    hints=dict(node.hints),
                    fingerprint=node.fingerprint,
                    recorded_strategy=ResolutionStrategy.SEMANTIC_EXACT,
                ),
                ResolutionStrategy.SEMANTIC_EXACT,
                unique=True,
            )

    # 2. The text beside it, for nodes with no unique name of their own.
    anchor = _anchor_for(node, snapshot)
    if anchor:
        descriptor = TargetDescriptor(
            role=node.role,
            scope=scope,
            anchor=anchor,
            hints=dict(node.hints),
            fingerprint=node.fingerprint,
            recorded_strategy=ResolutionStrategy.STRUCTURAL_ANCHOR,
        )
        return Synthesis(descriptor, ResolutionStrategy.STRUCTURAL_ANCHOR, unique=True)

    # 3. Position, as a last resort -- and flagged as not uniquely described.
    ordinal = next((i for i, n in enumerate(in_scope) if n.node_id == node.node_id), 0)
    return Synthesis(
        TargetDescriptor(
            role=node.role,
            name=NameMatch(value=node.name) if node.name else None,
            scope=scope,
            ordinal=ordinal,
            hints=dict(node.hints),
            fingerprint=node.fingerprint,
            recorded_strategy=ResolutionStrategy.ORDINAL_IN_REGION,
        ),
        ResolutionStrategy.ORDINAL_IN_REGION,
        unique=False,
    )
