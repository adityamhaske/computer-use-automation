"""Assembling raw surface elements into a normalized `UiSnapshot`.

Pure and surface-neutral: this is where a browser's accessibility tree and a desktop app's UIA tree
stop being different kinds of thing.

The important property is **stable identity**. Node ids are derived from structural position, so two
observations of an unchanged screen produce byte-identical ids. Everything downstream depends on
that: the determinism check compares traces, drift detection compares fingerprints, and handoff
reconciliation diffs the snapshot before and after a human's work. An id derived from a counter or
from traversal order would quietly break all three while looking fine.
"""

from __future__ import annotations

from datetime import datetime

from cua.domain.snapshot import NodeScope, UiNode, UiSnapshot
from cua.perception.normalize import (
    fingerprint,
    infer_region,
    is_noise,
    make_node_id,
    normalize_name,
    normalize_role,
)
from cua.perception.raw import RawElement


def build_snapshot(
    elements: list[RawElement],
    *,
    snapshot_id: str,
    url: str,
    title: str = "",
    http_status: int | None = None,
    captured_at: datetime | None = None,
    hint_builder: object = None,
) -> UiSnapshot:
    """Normalize raw elements into a snapshot, in document order.

    `hint_builder`, when supplied, is a callable `(role, attributes) -> str | None` that a driver
    uses to attach a surface-specific fast-path hint. Typed loosely because `perception` may not
    import a driver to name its type -- the boundary costs a little precision here and buys
    portability everywhere else.
    """
    by_id = {element.element_id: element for element in elements}
    children_of = {element.element_id: list(element.child_ids) for element in elements}

    roots = [
        element
        for element in elements
        if element.parent_id is None or element.parent_id not in by_id
    ]

    # Kept as (node, surface_id) pairs so the second pass needs no reverse lookup -- `observe()`
    # runs on every step of every discovery run, and a linear scan per node would make snapshotting
    # quadratic in page size on exactly the deeply-nested pages this project targets.
    built: list[tuple[UiNode, str]] = []
    node_ids: dict[str, str] = {}

    def visit(
        element: RawElement,
        path: list[tuple[str, int]],
        ancestor_roles: list[str],
        region: str | None,
        parent_node_id: str | None,
    ) -> None:
        role = normalize_role(element.role)
        name = normalize_name(element.name)

        node_id = make_node_id(element.frame, path)
        node_ids[element.element_id] = node_id

        # A heading re-labels the region for everything beneath it, which is what lets a descriptor
        # say "the Search button in the search form" on a page carrying two.
        if role == "heading":
            region = infer_region(name) or region

        hints: dict[str, str] = {}
        if element.attributes and callable(hint_builder) and (
            hint := hint_builder(role, element.attributes)
        ):
            hints["css"] = str(hint)
        if element.native_handle is not None:
            hints["native"] = str(element.native_handle)

        built.append(
            (
                UiNode(
                    node_id=node_id,
                    role=role,
                    name=name,
                    value=element.value,
                    states=frozenset(element.states),
                    scope=NodeScope(frame=element.frame, region=region),
                    parent_id=parent_node_id,
                    child_ids=(),  # filled in below, once every child has an id
                    hints=hints,
                    fingerprint=fingerprint(role, name, ancestor_roles, element.frame),
                ),
                element.element_id,
            )
        )

        seen_roles: dict[str, int] = {}
        for child_id in children_of.get(element.element_id, []):
            child = by_id.get(child_id)
            if child is None or is_noise(child.role):
                continue
            child_role = normalize_role(child.role)
            index = seen_roles.get(child_role, 0)
            seen_roles[child_role] = index + 1
            visit(
                child,
                [*path, (child_role, index)],
                [*ancestor_roles, role],
                region,
                node_id,
            )

    for index, root in enumerate(roots):
        if is_noise(root.role):
            continue
        root_role = normalize_role(root.role)
        visit(root, [(root_role, index)], [], None, None)

    # Second pass: child_ids expressed as stable node ids rather than surface ids. Deferred to a
    # second pass because a parent is visited before its children have ids.
    resolved = tuple(
        node.model_copy(
            update={
                "child_ids": tuple(
                    node_ids[child_id]
                    for child_id in children_of.get(surface_id, [])
                    if child_id in node_ids
                )
            }
        )
        for node, surface_id in built
    )

    return UiSnapshot(
        snapshot_id=snapshot_id,
        url=url,
        title=title,
        nodes=resolved,
        http_status=http_status,
        captured_at=captured_at,
    )
