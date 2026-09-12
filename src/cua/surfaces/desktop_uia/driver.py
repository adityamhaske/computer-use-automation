"""Windows UI Automation driver -- INTERFACE ONLY, deliberately unimplemented.

This file exists to make one claim concrete rather than rhetorical: **a capability artifact recorded
against a web surface would replay here without being rewritten.** The reason is that the artifact
never mentions a DOM, a CSS selector, or a browser. It says `role: button, name: "Search"`, and UIA
has both of those concepts.

What a real implementation would do:

    observe()      walk the UIA element tree from the app's root window, emit RawElement per
                   element, hand them to `cua.perception.build.build_snapshot`. Identical from
                   there on -- normalization, node ids, fingerprints and anchoring are all shared.

    dispatch()     UIA control patterns instead of DOM calls: InvokePattern for a click,
                   ValuePattern for typing, SelectionItemPattern for a dropdown.

    screenshot()   PrintWindow or a desktop capture, with the same redaction contract.

    bounds_for()   BoundingRectangle, which UIA gives directly.

The vocabulary mapping is already in `cua.perception.normalize.ROLE_ALIASES` (`Edit` -> textbox,
`Hyperlink` -> link, `DataItem` -> cell, ...), so the translation layer is written even though the
driver is not.

**What would genuinely be harder**, stated rather than glossed over:

- No frames, but a window/pane hierarchy with its own traversal quirks, and modal dialogs that are
  separate top-level windows rather than nodes in one tree.
- Many enterprise Win32 apps expose almost nothing through UIA -- a grid may surface as a single
  opaque element. There the ladder would have to fall through to the vision rung, which is exactly
  why vision stays in the ladder despite being disabled during replay.
- No equivalent of `Input.insertText`; typing means synthesizing key events at the OS level, with
  focus-stealing races a browser does not have.

Per AGENTS.md scope discipline: if this file grows past this docstring and the protocol below,
stop. A half-built desktop driver would be worse than an honest seam.
"""

from __future__ import annotations

from cua.domain.snapshot import Rect, UiSnapshot
from cua.policy.authorized import AuthorizedAction
from cua.surfaces.base import ActionResult, SessionInfo

CAPABILITIES: tuple[str, ...] = ("semantic_tree", "screenshot", "raw_input")
"""What this driver would declare. Note it matches the web driver's vocabulary minus `frames` --
which is how a capability declaring `driver_capabilities: [semantic_tree]` stays portable."""


class DesktopUiaDriver:
    """Satisfies `SurfaceDriver` structurally; every method is unimplemented on purpose."""

    def __init__(self, *, app_path: str) -> None:
        self._app_path = app_path
        raise NotImplementedError(
            "The desktop surface is a documented seam, not a built driver. "
            "See this module's docstring and REPORT.md §4."
        )

    def observe(self) -> UiSnapshot:  # pragma: no cover - seam
        raise NotImplementedError

    def dispatch(self, action: AuthorizedAction) -> ActionResult:  # pragma: no cover - seam
        raise NotImplementedError

    def screenshot(self, *, redact: tuple[Rect, ...] = ()) -> bytes:  # pragma: no cover - seam
        raise NotImplementedError

    def bounds_for(self, node_ids: tuple[str, ...]) -> dict[str, Rect]:  # pragma: no cover - seam
        raise NotImplementedError

    def session_info(self) -> SessionInfo:  # pragma: no cover - seam
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - seam
        raise NotImplementedError
