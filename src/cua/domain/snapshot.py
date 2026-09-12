"""The normalized view of a surface.

`UiSnapshot` is the cross-surface abstraction ([ADR 0001]). A driver produces one from whatever its
surface offers -- a browser accessibility tree today, a Windows UIA element tree or an OCR pass
tomorrow -- and everything above this line depends only on the normalized shape.

That is the seam the brief asks us to name: *how we perceive a surface* lives below it, and *the
recorded flow* lives above it. The artifact never learns what a DOM is, which is why the same
artifact could replay against a desktop driver.

Nodes are held in **document order**, which is load-bearing rather than incidental: it is the
tie-break the resolver uses when two candidates score equally, and a tie-break that depends on
dict or set iteration order would make replay nondeterministic.

[ADR 0001]: ../../../docs/adr/0001-uisnapshot-as-the-cross-surface-abstraction.md
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from functools import cached_property

from pydantic import BaseModel, ConfigDict, Field


class Rect(BaseModel):
    """Bounding box in surface coordinates.

    Present for evidence (screenshot redaction) and for vision fallback during discovery. Never used
    to identify a control during replay -- coordinates are the least stable thing you can persist.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    x: float
    y: float
    width: float
    height: float


class NodeScope(BaseModel):
    """Where a node lives, in surface-neutral terms.

    `frame` matters more than it looks: on a frameset app the same accessible name can appear in the
    navigation frame and the content frame, and resolving to the wrong one is a wrong click. Scoping
    a descriptor to a frame is often the difference between unique and ambiguous.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    frame: str | None = None
    """Frame name, e.g. "content". None for the top-level document."""

    region: str | None = None
    """Inferred logical region, e.g. "search_form". Best-effort; never required for correctness."""


class UiNode(BaseModel):
    """One control or piece of content, described semantically."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    """Stable within a snapshot AND across snapshots of the same screen.

    Derived from structural position + role + name -- never from a counter or a memory address, or
    two observations of an unchanged page would disagree and determinism would be lost.
    """

    role: str
    """Normalized semantic role: textbox, button, link, cell, row, heading, table, ...

    Normalized because surfaces disagree on spelling -- a browser says `textbox` where UIA says
    `Edit`. The normalization happens in the driver so nothing above has to care.
    """

    name: str | None = None
    """Accessible name. Often absent on legacy surfaces -- hence `structural_anchor`."""

    value: str | None = None
    states: frozenset[str] = Field(default_factory=frozenset)
    """e.g. disabled, focused, required, checked, expanded."""

    bounds: Rect | None = None
    scope: NodeScope = Field(default_factory=NodeScope)

    parent_id: str | None = None
    child_ids: tuple[str, ...] = ()

    hints: dict[str, str] = Field(default_factory=dict)
    """Driver-specific identifiers (css path, backend node id).

    A cache, never an identity. The resolver may use a hint as a fast path but must re-verify the
    node semantically before accepting it -- see docs/design/target-resolution.md.
    """

    fingerprint: str = ""
    """Structural signature, for drift detection between discovery and replay."""

    @property
    def is_interactive(self) -> bool:
        return self.role in {
            "textbox",
            "button",
            "link",
            "combobox",
            "listbox",
            "checkbox",
            "radio",
            "menuitem",
            "tab",
            "searchbox",
            "spinbutton",
            "slider",
        }

    @property
    def is_enabled(self) -> bool:
        return "disabled" not in self.states


class UiSnapshot(BaseModel):
    """One observation of a surface, at one moment.

    Immutable. A replay compares snapshots to detect what changed after an action, and to reconcile
    state after a human hands control back.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    url: str
    title: str = ""
    nodes: tuple[UiNode, ...] = ()
    """Document order. Load-bearing: this is the resolver's deterministic tie-break."""

    captured_at: datetime | None = None
    http_status: int | None = None
    """When the driver can observe it. Lets a recovery rule match on 502/503/504 honestly rather
    than inferring a transient failure from page text."""

    @cached_property
    def _by_id(self) -> dict[str, UiNode]:
        return {n.node_id: n for n in self.nodes}

    def node(self, node_id: str) -> UiNode | None:
        return self._by_id.get(node_id)

    def children(self, node: UiNode) -> tuple[UiNode, ...]:
        return tuple(c for cid in node.child_ids if (c := self._by_id.get(cid)) is not None)

    def parent(self, node: UiNode) -> UiNode | None:
        return self._by_id.get(node.parent_id) if node.parent_id else None

    def ancestors(self, node: UiNode) -> Iterator[UiNode]:
        """Walk upward. Bounded by construction -- the tree has no cycles."""
        current = self.parent(node)
        seen: set[str] = set()
        while current is not None and current.node_id not in seen:
            seen.add(current.node_id)
            yield current
            current = self.parent(current)

    def descendants(self, node: UiNode) -> Iterator[UiNode]:
        """Depth-first, in document order."""
        for child in self.children(node):
            yield child
            yield from self.descendants(child)

    def nearest_ancestor(self, node: UiNode, role: str) -> UiNode | None:
        """The closest enclosing node of `role` -- e.g. the row a textbox sits in.

        This is the primitive `structural_anchor` is built on: legacy apps identify a control by the
        text in the cell next to it, not by anything on the control itself.
        """
        return next((a for a in self.ancestors(node) if a.role == role), None)

    def find(self, *, role: str | None = None, frame: str | None = None) -> tuple[UiNode, ...]:
        """All matching nodes, in document order."""
        return tuple(
            n
            for n in self.nodes
            if (role is None or n.role == role) and (frame is None or n.scope.frame == frame)
        )

    def text_content(self) -> str:
        """All visible text, for `text_present` style predicates."""
        return "\n".join(n.name for n in self.nodes if n.name)
