"""Rendering a `UiSnapshot` for a model.

The model is shown the *normalized snapshot*, never raw HTML. Three consequences, all deliberate:

**It reasons in the vocabulary the artifact stores.** When the model says "the textbox named Member
Number", that is already the shape a `TargetDescriptor` takes -- so a discovery trace is compilable
rather than needing to be reverse-engineered from DOM soup.

**It cannot see what perception cannot see.** If a control is missing from the snapshot the model
will not click it, which means discovery failures point at a perception gap rather than producing a
step that only works when a human is watching.

**Redaction is tractable.** Page text passes through the redactor on the way out. Doing that to raw
HTML would mean redacting attributes, inline scripts and URLs too.

Rendering is also where token budget is won or lost: a frameset back-office page produces several
hundred nodes, most of them structural. Only nodes a model could act on carry an id.
"""

from __future__ import annotations

from cua.domain.snapshot import UiNode, UiSnapshot
from cua.policy.redact import Redactor

ACTIONABLE: frozenset[str] = frozenset(
    {"textbox", "button", "link", "combobox", "checkbox", "radio", "searchbox", "option", "tab"}
)
"""Roles the model can act on."""

CONTEXT: frozenset[str] = frozenset({"cell", "heading", "text", "row", "table", "document"})

READABLE: frozenset[str] = frozenset({"cell", "heading", "text"})
"""Roles the model can *read from* via `extract`.

These carry ids too. An earlier version gave ids only to actionable controls, which quietly made
extraction impossible: on a legacy screen the value a capability needs -- an account balance -- sits
in a plain table cell, and a model cannot record what it cannot reference. Caught by the first
end-to-end run of the loop, whose whole goal was to read a balance.

The extra ids cost tokens. Being unable to express the output a capability exists to produce costs
the capability.
"""

MAX_NAME = 80


def _label(node: UiNode) -> str:
    name = (node.name or "").strip()
    if len(name) > MAX_NAME:
        name = name[: MAX_NAME - 1] + "…"
    parts = [node.role]
    if name:
        parts.append(f'"{name}"')
    if node.value:
        value = node.value if len(node.value) <= 40 else node.value[:39] + "…"
        parts.append(f"value={value!r}")
    if "disabled" in node.states:
        parts.append("[disabled]")
    if "required" in node.states:
        parts.append("[required]")
    return " ".join(parts)


def render_snapshot(
    snapshot: UiSnapshot,
    *,
    redactor: Redactor | None = None,
    max_nodes: int = 220,
) -> str:
    """A compact, indented view of the page, with ids only on actionable controls.

    Rows are kept even when unnamed: they are what makes "the field in the row labelled Account
    Type" visible to the model, and that relationship is the one thing legacy screens actually
    provide.
    """
    lines: list[str] = [
        f"URL: {snapshot.url}",
        f"TITLE: {snapshot.title}",
    ]
    if snapshot.http_status and snapshot.http_status >= 400:
        lines.append(f"HTTP STATUS: {snapshot.http_status}")
    lines.append("")

    depth_of: dict[str, int] = {}
    shown = 0

    for node in snapshot.nodes:
        parent_depth = depth_of.get(node.parent_id or "", -1)
        depth = parent_depth + 1
        depth_of[node.node_id] = depth

        interesting = node.role in ACTIONABLE or (node.role in CONTEXT and node.name)
        if node.role == "row":
            interesting = True
        if not interesting:
            continue

        if shown >= max_nodes:
            lines.append(f"  … {len(snapshot.nodes) - shown} more nodes not shown")
            break

        indent = "  " * min(depth, 8)
        frame = f"[{node.scope.frame}] " if node.scope.frame else ""
        entry = f"{indent}{frame}{_label(node)}"
        if node.role in ACTIONABLE or (node.role in READABLE and node.name):
            entry += f"   #{node.node_id}"
        lines.append(entry)
        shown += 1

    rendered = "\n".join(lines)
    return redactor.text(rendered) if redactor else rendered
