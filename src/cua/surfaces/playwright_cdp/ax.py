"""Extracting a semantic tree from Chromium over CDP.

The one non-obvious thing here, and the reason this module exists separately: **the accessibility
tree does not cross frame boundaries.** `Accessibility.getFullAXTree` on a frameset page returns
three nodes -- a root and two opaque `Iframe` entries -- and none of the content. On the legacy
apps this project targets, framesets are the norm, so a naive implementation perceives nothing at
all and the failure looks like an empty page rather than a missing call.

The fix is to enumerate frames via `Page.getFrameTree` and request the tree once per frame, then
stitch the results. Two approaches that do *not* work, recorded so nobody re-derives them:

- `BrowserContext.new_cdp_session(frame)` -- *"This frame does not have a separate CDP session, it
  is part of the parent frame's session."* Same-process frames have no session of their own.
- A depth argument on the page-level call -- frames are a boundary, not a depth limit.

Measured in `scripts/spike_semantic_tree.py`; findings in
docs/adr/0001-uisnapshot-as-the-cross-surface-abstraction.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

INTERACTIVE_ROLES: frozenset[str] = frozenset(
    {"textbox", "button", "link", "combobox", "checkbox", "radio", "searchbox", "listbox"}
)
"""Roles worth paying a CDP round trip for, to collect attribute hints."""


@dataclass
class RawNode:
    """One node as the browser reports it, before normalization."""

    ax_id: str
    role: str
    name: str | None
    value: str | None
    states: set[str] = field(default_factory=set)
    child_ax_ids: list[str] = field(default_factory=list)
    parent_ax_id: str | None = None
    backend_node_id: int | None = None
    frame: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)


def frame_ids(cdp: Any) -> dict[str, str]:
    """Frame name -> frame id, for every frame in the page.

    The main frame is keyed `"main"`. Unnamed subframes are keyed by index so they remain
    addressable; a capability targeting one would be fragile, but perceiving it is still better
    than silently dropping its content.
    """
    ids: dict[str, str] = {}
    anonymous = 0

    def walk(node: dict[str, Any], *, is_root: bool) -> None:
        nonlocal anonymous
        frame = node["frame"]
        name = frame.get("name")
        if is_root:
            key = "main"
        elif name:
            key = name
        else:
            key = f"frame{anonymous}"
            anonymous += 1
        ids[key] = frame["id"]
        for child in node.get("childFrames", []):
            walk(child, is_root=False)

    walk(cdp.send("Page.getFrameTree")["frameTree"], is_root=True)
    return ids


def _string_value(node: dict[str, Any], key: str) -> str | None:
    holder = node.get(key)
    if not isinstance(holder, dict):
        return None
    value = holder.get("value")
    return value if isinstance(value, str) and value else None


def _states(node: dict[str, Any]) -> set[str]:
    """Interesting boolean properties, flattened into a state set.

    Only the ones that change whether an action is legal. `disabled` in particular matters: clicking
    a disabled control succeeds mechanically and does nothing, which is the kind of silent no-op
    that makes a replay report success while achieving nothing.
    """
    interesting = {"disabled", "focused", "required", "checked", "expanded", "readonly", "invalid"}
    states: set[str] = set()
    for prop in node.get("properties", []):
        name = prop.get("name")
        if name not in interesting:
            continue
        value = prop.get("value", {}).get("value")
        if value is True or value in ("true", "mixed"):
            states.add(name)
    return states


def fetch_tree(cdp: Any, frame_name: str, frame_id: str) -> list[RawNode]:
    """The semantic tree for one frame, ignored nodes removed."""
    response = cdp.send("Accessibility.getFullAXTree", {"frameId": frame_id})
    nodes: list[RawNode] = []
    for node in response.get("nodes", []):
        if node.get("ignored"):
            continue
        role = (node.get("role") or {}).get("value") or "unknown"
        nodes.append(
            RawNode(
                ax_id=node["nodeId"],
                role=role,
                name=_string_value(node, "name"),
                value=_string_value(node, "value"),
                states=_states(node),
                child_ax_ids=list(node.get("childIds", [])),
                parent_ax_id=node.get("parentId"),
                backend_node_id=node.get("backendDOMNodeId"),
                frame=frame_name,
            )
        )
    return nodes


def fetch_attributes(cdp: Any, nodes: list[RawNode]) -> None:
    """Attach DOM attributes to interactive nodes, in place.

    These become `TargetDescriptor.hints` -- the kind of brittle selector a naive automation would
    cache as identity (`input[name=memno].frmfld`). We keep them only as a *fast path* that must be
    re-verified semantically, which is precisely what the Variant B test proves: those hints all
    break across tenants, and resolution still succeeds.

    Uses `DOM.describeNode` rather than `DOM.getAttributes`: the latter takes a *frontend* nodeId,
    not the backendNodeId the accessibility tree gives us, so calling it with one throws on every
    node. That mistake is invisible without a test, because the failure mode is simply "no hints" --
    which looks exactly like a page whose controls have no attributes worth caching.

    Best-effort: a node whose DOM counterpart has gone is skipped rather than failing the snapshot.
    """
    for node in nodes:
        if node.role.lower() not in INTERACTIVE_ROLES or node.backend_node_id is None:
            continue
        try:
            described = cdp.send("DOM.describeNode", {"backendNodeId": node.backend_node_id})
        except Exception:
            continue
        pairs = described.get("node", {}).get("attributes", [])
        node.attributes = {
            pairs[i]: pairs[i + 1]
            for i in range(0, len(pairs) - 1, 2)
            if pairs[i] in ("name", "id", "class", "type", "href", "value")
        }


def css_hint(role: str, attributes: dict[str, str]) -> str | None:
    """A plausible CSS selector for this control, from its attributes.

    Deliberately the *naive* selector -- the one a scraper would write and a tenant's restyle would
    break. Storing it as a hint rather than as identity is the difference between an artifact that
    survives a redesign and one that does not.
    """
    if not attributes:
        return None
    tag = {"textbox": "input", "button": "input", "link": "a", "combobox": "select"}.get(role, "*")
    if name := attributes.get("name"):
        return f'{tag}[name="{name}"]'
    if css_class := attributes.get("class"):
        return f"{tag}.{css_class.split()[0]}"
    if href := attributes.get("href"):
        return f'a[href="{href}"]'
    return None
