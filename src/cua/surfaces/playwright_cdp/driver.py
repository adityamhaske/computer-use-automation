"""Chromium surface driver, over the Chrome DevTools Protocol.

Implements `SurfaceDriver` for web surfaces, including the frameset-based legacy apps this project
targets. Everything browser-specific stops here: above this module the system sees only
`UiSnapshot`, `TargetDescriptor` and `Action`.

Two decisions worth knowing about:

**Actions are dispatched against the node we perceived**, via its CDP backend node id -- not by
re-querying with a selector. Re-querying would reintroduce exactly the selector brittleness the
whole targeting design exists to avoid, and would open a window where the page changes between
resolution and action.

**`observe()` does not fetch geometry.** Bounds cost a round trip per node and almost nothing needs
them; `bounds_for()` fetches them when screenshot redaction or a vision fallback actually does.
`observe()` runs on every step of every run, so its cost compounds.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from cua.domain.action import Click, Navigate, PressKey, RawInput, Reload, Scroll, Select, Type
from cua.domain.snapshot import Rect, UiSnapshot
from cua.perception.build import build_snapshot
from cua.perception.raw import RawElement
from cua.policy.authorized import AuthorizedAction
from cua.surfaces.base import ActionResult, SessionInfo
from cua.surfaces.playwright_cdp.ax import css_hint, fetch_attributes, fetch_tree, frame_ids

CAPABILITIES: tuple[str, ...] = ("semantic_tree", "screenshot", "raw_input", "frames")

_CLICK_JS = "function() { this.click(); }"
_FOCUS_JS = "function() { this.focus(); this.scrollIntoView({block: 'center'}); }"
_CLEAR_JS = "function() { this.value = ''; }"
_SELECT_JS = """
function(wanted) {
  const target = String(wanted).toLowerCase();
  for (const option of this.options) {
    if (option.value.toLowerCase() === target || option.text.toLowerCase() === target) {
      this.value = option.value;
      this.dispatchEvent(new Event('change', {bubbles: true}));
      return true;
    }
  }
  return false;
}
"""
_SCROLL_JS = "function() { this.scrollIntoView({block: 'center'}); }"


class PlaywrightCdpDriver:
    """A live Chromium session, perceived semantically and acted on by node identity."""

    def __init__(
        self,
        *,
        headless: bool = True,
        session_id: str | None = None,
        viewport: tuple[int, int] = (1280, 900),
    ) -> None:
        self._session_id = session_id or f"sess-{uuid.uuid4().hex[:10]}"
        self._playwright = sync_playwright().start()
        self._browser: Browser = self._playwright.chromium.launch(headless=headless)
        self._context: BrowserContext = self._browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]}
        )
        self._page: Page = self._context.new_page()
        self._cdp = self._context.new_cdp_session(self._page)
        self._cdp.send("DOM.enable")
        self._cdp.send("Accessibility.enable")

        # node_id -> CDP backend node id, refreshed on every observe(). This is how an action
        # reaches the node that was actually resolved.
        self._handles: dict[str, int] = {}
        # Per-frame navigation status. On a frameset app the interesting failure is almost never
        # in the main document: the shell loads fine and the *content frame* returns the 502.
        # Tracking only the main frame made transient-failure recovery unreachable on exactly the
        # kind of application this project targets.
        self._frame_status: dict[str, int] = {}
        self._page.on("response", self._note_response)

    # ------------------------------------------------------------- plumbing
    def _note_response(self, response: Any) -> None:
        """Remember each frame's navigation status, so a recovery rule can match on 502/503/504.

        Matching transport status beats matching page text: a rule keyed on the words "Service
        Unavailable" would also fire on a member whose name happened to contain them.
        """
        try:
            if response.request.is_navigation_request():
                # Keyed by the document URL being loaded rather than by the frame, because a frame
                # that has not finished loading reports an empty name and an empty url -- so frame
                # identity is not yet usable at the moment the response arrives.
                self._frame_status[response.url] = response.status
        except Exception:
            pass

    @property
    def _last_status(self) -> int | None:
        """The status a recovery rule should reason about.

        An error in *any* frame wins over a success in another. On a frameset app the shell loads
        perfectly around a content frame that returned 502, and reporting 200 for that page would
        leave the declared transient-failure recovery permanently unreachable -- on exactly the kind
        of application this project exists to automate.
        """
        if not self._frame_status:
            return None
        errors = [status for status in self._frame_status.values() if status >= 400]
        if errors:
            return max(errors)
        return next(iter(self._frame_status.values()))

    def _object_id(self, node_id: str) -> str | None:
        backend_id = self._handles.get(node_id)
        if backend_id is None:
            return None
        try:
            resolved = self._cdp.send("DOM.resolveNode", {"backendNodeId": backend_id})
        except Exception:
            return None
        object_id = resolved.get("object", {}).get("objectId")
        return str(object_id) if object_id else None

    def _call(self, node_id: str, js: str, *args: Any) -> tuple[bool, Any]:
        object_id = self._object_id(node_id)
        if object_id is None:
            return False, None
        response = self._cdp.send(
            "Runtime.callFunctionOn",
            {
                "functionDeclaration": js,
                "objectId": object_id,
                "arguments": [{"value": arg} for arg in args],
                "returnByValue": True,
            },
        )
        if "exceptionDetails" in response:
            return False, None
        return True, response.get("result", {}).get("value")

    # -------------------------------------------------------------- observe

    def observe(self) -> UiSnapshot:
        """Capture every frame's semantic tree and stitch them into one snapshot.

        The per-frame loop is the whole point: `Accessibility.getFullAXTree` at page level stops at
        frame boundaries and returns three nodes for a frameset app. See `ax.py`.
        """
        elements: list[RawElement] = []
        self._handles = {}

        for frame_name, frame_id in frame_ids(self._cdp).items():
            try:
                raw_nodes = fetch_tree(self._cdp, frame_name, frame_id)
            except Exception:
                continue
            fetch_attributes(self._cdp, raw_nodes)
            elements.extend(
                RawElement(
                    element_id=f"{frame_name}:{node.ax_id}",
                    role=node.role,
                    name=node.name,
                    value=node.value,
                    states=node.states,
                    child_ids=[f"{frame_name}:{child}" for child in node.child_ax_ids],
                    parent_id=f"{frame_name}:{node.parent_ax_id}" if node.parent_ax_id else None,
                    frame=None if frame_name == "main" else frame_name,
                    attributes=node.attributes,
                    native_handle=node.backend_node_id,
                )
                for node in raw_nodes
            )

        snapshot = build_snapshot(
            elements,
            snapshot_id=f"snap-{uuid.uuid4().hex[:10]}",
            url=self._page.url,
            title=self._page.title(),
            http_status=self._last_status,
            captured_at=datetime.now(UTC),
            hint_builder=css_hint,
        )

        for node in snapshot.nodes:
            if native := node.hints.get("native"):
                self._handles[node.node_id] = int(native)
        return snapshot

    # ------------------------------------------------------------- dispatch

    def dispatch(self, action: AuthorizedAction) -> ActionResult:
        """Perform an authorized action. The type signature is the chokepoint."""
        started = time.perf_counter()
        before_url = self._page.url
        inner = action.action
        node_id = action.resolved_node_id

        try:
            ok, message = self._perform(inner, node_id)
        except Exception as exc:
            ok, message = False, f"{type(exc).__name__}: {exc}"

        return ActionResult(
            ok=ok,
            navigated=self._page.url != before_url,
            http_status=self._last_status,
            message=message,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _perform(self, inner: Any, node_id: str | None) -> tuple[bool, str]:
        if isinstance(inner, Navigate):
            # Statuses describe *the current page load*. Clearing first means a 502 from the page
            # we are leaving cannot make the page we are arriving at look broken -- which would
            # leave a declared recovery rule firing forever against a condition that had cleared.
            self._frame_status.clear()
            self._page.goto(inner.url, wait_until="load")
            return True, ""

        if isinstance(inner, Reload):
            self._frame_status.clear()
            self._page.reload(wait_until="load")
            return True, ""

        if isinstance(inner, RawInput):
            return self._perform_raw_input(inner)

        if isinstance(inner, PressKey) and node_id is None:
            self._page.keyboard.press(inner.key)
            return True, ""

        if node_id is None:
            return False, f"{inner.type} requires a resolved target"

        if node_id not in self._handles:
            return False, f"node {node_id} is not present in the current snapshot"

        if isinstance(inner, Click):
            ok, _ = self._call(node_id, _CLICK_JS)
            return ok, "" if ok else "click did not reach the element"

        if isinstance(inner, Type):
            return self._perform_type(inner, node_id)

        if isinstance(inner, Select):
            ok, matched = self._call(node_id, _SELECT_JS, str(inner.value))
            if not ok:
                return False, "select element was unreachable"
            return bool(matched), "" if matched else f"no option matching {inner.value!r}"

        if isinstance(inner, PressKey):
            self._call(node_id, _FOCUS_JS)
            self._page.keyboard.press(inner.key)
            return True, ""

        if isinstance(inner, Scroll):
            ok, _ = self._call(node_id, _SCROLL_JS)
            return ok, "" if ok else "scroll target was unreachable"

        # extract / assert / wait_for are evaluated against snapshots by the executor and never
        # reach a driver -- they change nothing about the surface.
        return False, f"{inner.type} is not a driver-level action"

    def _perform_type(self, inner: Type, node_id: str) -> tuple[bool, str]:
        """Focus, optionally clear, then insert text as real input events.

        `Input.insertText` rather than assigning `value` directly: legacy screens attach validation
        and formatting to input events, and a value assignment that skips them produces a field that
        looks correct and behaves as though nothing was entered.
        """
        ok, _ = self._call(node_id, _FOCUS_JS)
        if not ok:
            return False, "could not focus the field"
        if inner.clear_first:
            self._call(node_id, _CLEAR_JS)
        self._cdp.send("Input.insertText", {"text": str(inner.value)})
        return True, ""

    def _perform_raw_input(self, inner: RawInput) -> tuple[bool, str]:
        """Human console input, dispatched through the same path as everything else.

        The console submits these as actions rather than injecting CDP events directly, so a human
        operator's clicks are authorized, classified and recorded exactly like automation's. See
        ADR 0004.
        """
        if inner.kind == "mouse_click" and inner.x is not None and inner.y is not None:
            self._page.mouse.click(inner.x, inner.y)
            return True, ""
        if inner.kind == "mouse_move" and inner.x is not None and inner.y is not None:
            self._page.mouse.move(inner.x, inner.y)
            return True, ""
        if inner.kind == "key" and inner.key:
            self._page.keyboard.press(inner.key)
            return True, ""
        if inner.kind == "text" and inner.text:
            self._page.keyboard.type(inner.text)
            return True, ""
        return False, f"incomplete raw input: {inner.kind}"

    # ------------------------------------------------------------- evidence

    def screenshot(self, *, redact: tuple[Rect, ...] = ()) -> bytes:
        """A PNG of the current page, with `redact` regions painted over.

        Redaction happens before the bytes exist, not after: a screenshot written to disk and
        redacted later has already been a plaintext copy of regulated data on the filesystem.
        """
        if redact:
            self._page.evaluate(_OVERLAY_JS, [r.model_dump() for r in redact])
        try:
            return self._page.screenshot(full_page=False)
        finally:
            if redact:
                self._page.evaluate(_REMOVE_OVERLAY_JS)

    def bounds_for(self, node_ids: tuple[str, ...]) -> dict[str, Rect]:
        bounds: dict[str, Rect] = {}
        for node_id in node_ids:
            backend_id = self._handles.get(node_id)
            if backend_id is None:
                continue
            try:
                box = self._cdp.send("DOM.getBoxModel", {"backendNodeId": backend_id})
            except Exception:
                continue
            quad = box.get("model", {}).get("border", [])
            if len(quad) >= 6:
                bounds[node_id] = Rect(
                    x=quad[0], y=quad[1], width=quad[2] - quad[0], height=quad[5] - quad[1]
                )
        return bounds

    # -------------------------------------------------------------- session

    def session_info(self) -> SessionInfo:
        return SessionInfo(
            session_id=self._session_id,
            driver="playwright_cdp",
            url=self._page.url,
            capabilities=CAPABILITIES,
        )

    @property
    def page(self) -> Page:
        """The live page.

        Exposed for the operator console's screencast, which needs a CDP session on this exact page
        -- the human must drive the session the automation was using, not a copy of it.
        """
        return self._page

    def close(self) -> None:
        for shutdown in (self._context.close, self._browser.close, self._playwright.stop):
            # Teardown failures must not mask the real failure that caused teardown.
            with contextlib.suppress(Exception):
                shutdown()


_OVERLAY_JS = """
(rects) => {
  const layer = document.createElement('div');
  layer.id = '__cua_redaction__';
  for (const r of rects) {
    const box = document.createElement('div');
    box.style.cssText = `position:fixed;left:${r.x}px;top:${r.y}px;` +
      `width:${r.width}px;height:${r.height}px;background:#111;z-index:2147483647;`;
    layer.appendChild(box);
  }
  document.documentElement.appendChild(layer);
}
"""

_REMOVE_OVERLAY_JS = """
() => { document.getElementById('__cua_redaction__')?.remove(); }
"""
