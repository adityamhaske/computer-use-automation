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
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from cua.domain.action import Click, Navigate, PressKey, RawInput, Reload, Scroll, Select, Type
from cua.domain.ids import new_id
from cua.domain.snapshot import Rect, UiSnapshot
from cua.perception.build import build_snapshot
from cua.perception.raw import RawElement
from cua.policy.authorized import AuthorizedAction
from cua.surfaces.base import ActionResult, SessionInfo
from cua.surfaces.playwright_cdp.ax import css_hint, fetch_attributes, fetch_tree, frame_ids

CAPABILITIES: tuple[str, ...] = ("semantic_tree", "screenshot", "raw_input", "frames")

# Documents a frame holds before it has loaded anything, or after a navigation was refused. Not a
# destination the page chose, so not an allowlist question -- every frameset frame passes through
# `about:blank` on its way to its real `src`, and a refused navigation leaves `chrome-error://`.
_NOT_DESTINATIONS = ("about:blank", "about:srcdoc", "chrome-error://")


_VETTING_TIMEOUT_S = 10.0
"""Upper bound on waiting for the navigation guard's verdict -- a slow target's response time."""


def _is_destination(url: str) -> bool:
    return bool(url) and not url.startswith(_NOT_DESTINATIONS)


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
        self._session_id = session_id or new_id("sess", 10)
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

        # Where frames went, and where they were stopped from going, since the last dispatch began.
        # Recorded from events rather than read off `page.url` afterwards: on a frameset app the
        # top-level URL never changes, so comparing it said "did not navigate" while the content
        # frame was off on another host entirely.
        self._landed: list[str] = []
        self._refused: list[str] = []
        self._guard: Callable[[str], bool] | None = None
        self._vetting = 0
        """Navigations the route is deciding on right now. A dispatch does not report until this is
        zero, or a redirect refused a moment later would be reported as a success."""
        self._page.on("framenavigated", self._note_frame_navigated)
        # Routed from construction, on the thread that owns the browser. Synchronous Playwright is
        # bound to its creating thread, and a dispatcher -- which installs the guard -- is often
        # built on another one (the console builds it on the server's thread).
        self._context.route("**/*", self._route)

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

    def _note_frame_navigated(self, frame: Any) -> None:
        with contextlib.suppress(Exception):
            if _is_destination(frame.url):
                self._landed.append(frame.url)

    def _route(self, route: Any) -> None:
        """Refuse an off-list document navigation in any frame, including one reached by redirect.

        A route sees the request a navigation *starts* with, not the hops a redirect adds -- the
        browser follows those itself. So a permitted navigation is fetched here with redirects off:
        a `Location` outside the allowlist is refused before the browser can follow it, and
        anything else is handed back to the browser as the response it would have received. A
        fulfilled redirect is followed as a new request, which comes back through this route.
        """
        request = route.request
        if self._guard is None or not request.is_navigation_request():
            route.continue_()
            return
        self._vetting += 1
        try:
            if not self._guard(request.url):
                self._refuse(route, request.url)
                return
            response = route.fetch(max_redirects=0)
            location = response.headers.get("location")
            if 300 <= response.status < 400 and location:
                destination = urljoin(request.url, location)
                if not self._guard(destination):
                    self._refuse(route, destination)
                    return
            route.fulfill(response=response)
        except Exception:
            # A navigation the guard could not vet has not been permitted.
            self._refuse(route, request.url)
        finally:
            self._vetting -= 1

    def _refuse(self, route: Any, url: str) -> None:
        self._refused.append(url)
        with contextlib.suppress(Exception):
            route.abort("blockedbyclient")

    def set_navigation_guard(self, guard: Callable[[str], bool]) -> None:
        """Install the allowlist at the surface. Applies to every frame and every later navigation,
        including one the page starts by itself after the action that caused it has returned.

        Touches no browser state, so it is safe to call from any thread."""
        self._guard = guard

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
            snapshot_id=new_id("snap", 10),
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
        before = self._frame_urls()
        self._landed.clear()
        # `_refused` is drained, not cleared: a navigation the page started on its own after an
        # earlier action returned may have been refused since, and it must still be reported.
        inner = action.action
        node_id = action.resolved_node_id

        try:
            ok, message = self._perform(inner, node_id)
            # One round trip to the page before reading what happened. A scripted click starts its
            # navigation as the call returns, so reading straight away reported "went nowhere" for
            # a link that was, at that moment, on its way off the allowlist. A round trip is a
            # condition, not a sleep: it costs a few milliseconds and orders the events before it.
            self._page.evaluate("0")
            self._await_vetting()
        except Exception as exc:
            ok, message = False, f"{type(exc).__name__}: {exc}"

        after = self._frame_urls()
        changed = [url for frame, url in after.items() if before.get(frame) != url]
        landed = tuple(dict.fromkeys([*self._landed, *changed]))
        return ActionResult(
            ok=ok,
            navigated=bool(landed),
            landed_urls=landed,
            refused_navigations=self._drain_refused(),
            http_status=self._last_status,
            message=message,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _await_vetting(self) -> None:
        """Let the route finish deciding on any navigation this action started.

        Waiting on a condition with a declared bound, not a duration: it returns the moment nothing
        is being vetted, which for an action that did not navigate is immediately.
        """
        deadline = time.monotonic() + _VETTING_TIMEOUT_S
        while self._vetting and time.monotonic() < deadline:
            self._page.wait_for_timeout(10)

    def _drain_refused(self) -> tuple[str, ...]:
        refused = tuple(dict.fromkeys(self._refused))
        self._refused.clear()
        return refused

    def _frame_urls(self) -> dict[int, str]:
        """Every frame's current document URL, keyed by frame identity."""
        return {id(frame): frame.url for frame in self._page.frames if _is_destination(frame.url)}

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
