"""The driver perceives the hostile app correctly, against a real browser.

These are the Phase 03 exit criteria, and they exist because the semantic-tree bet is the
load-bearing assumption of the whole design. A spike measured it once
(`scripts/spike_semantic_tree.py`); these tests keep it measured.

Marked `browser` and `slow`: they launch Chromium and boot the mock app.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn
from apps.mock_bank.server import VALID_PW, VALID_USER, create_app

from cua.perception.normalize import normalize_role
from cua.surfaces.playwright_cdp.driver import PlaywrightCdpDriver

pytestmark = [pytest.mark.browser, pytest.mark.slow]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Server:
    """The mock app on a background thread, so tests drive a real HTTP surface."""

    def __init__(self, variant: str = "base") -> None:
        self.port = _free_port()
        config = uvicorn.Config(
            create_app(variant), host="127.0.0.1", port=self.port, log_level="error"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> _Server:
        self._thread.start()
        for _ in range(100):
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("mock app did not start")

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@pytest.fixture(scope="module")
def base_app() -> Iterator[_Server]:
    with _Server("base") as server:
        yield server


@pytest.fixture(scope="module")
def variant_b_app() -> Iterator[_Server]:
    with _Server("variant_b") as server:
        yield server


@pytest.fixture
def driver() -> Iterator[PlaywrightCdpDriver]:
    instance = PlaywrightCdpDriver(headless=True)
    try:
        yield instance
    finally:
        instance.close()


def sign_in(driver: PlaywrightCdpDriver, base_url: str) -> None:
    """Sign in with Playwright directly.

    Deliberately not going through the policy chokepoint: these tests are about *perception*, and
    arriving at a signed-in page is setup, not the thing under test.
    """
    page = driver.page
    page.goto(f"{base_url}/login")
    page.fill('input[name="user"]', VALID_USER)
    page.fill('input[name="pw"]', VALID_PW)
    page.click('input[type="submit"]')
    page.wait_for_load_state()


# ------------------------------------------------------------------- frames


def test_observe_sees_inside_frames(driver: PlaywrightCdpDriver, base_app: _Server) -> None:
    """The finding that shaped this phase.

    A page-level accessibility tree on a frameset returns three nodes -- a root and two opaque
    iframe entries -- and none of the content. If the driver did not stitch per-frame trees, this
    snapshot would be empty and the failure would look like a blank page rather than a missing call.
    """
    sign_in(driver, base_app.url)
    driver.page.goto(base_app.url)
    driver.page.wait_for_load_state()

    snapshot = driver.observe()

    assert len(snapshot.nodes) > 20, (
        "frame content is missing -- per-frame stitching is not working"
    )
    frames = {node.scope.frame for node in snapshot.nodes}
    assert "content" in frames
    assert "nav" in frames


def test_controls_carry_semantic_names(driver: PlaywrightCdpDriver, base_app: _Server) -> None:
    """`semantic_exact` is viable on this surface -- the first rung of the ladder has something
    to match on, even on deliberately hostile markup."""
    sign_in(driver, base_app.url)
    driver.page.goto(f"{base_app.url}/search")
    snapshot = driver.observe()

    textboxes = {node.name for node in snapshot.nodes if node.role == "textbox"}
    buttons = {node.name for node in snapshot.nodes if node.role == "button"}
    assert "Member Number" in textboxes
    assert "Search" in buttons


def test_row_structure_supports_anchoring(driver: PlaywrightCdpDriver, base_app: _Server) -> None:
    """`structural_anchor` is viable: row -> label cell -> value cell is explicit in the tree.

    This is what makes legacy screens tractable at all. The sub-account controls have no accessible
    name whatsoever, so structure is the only way in.
    """
    sign_in(driver, base_app.url)
    driver.page.goto(f"{base_app.url}/member/12345")
    snapshot = driver.observe()

    label = next(
        node for node in snapshot.nodes if node.role == "cell" and node.name == "Savings Balance"
    )
    row = snapshot.nearest_ancestor(label, "row")
    assert row is not None

    cells = [node.name for node in snapshot.descendants(row) if node.role == "cell"]
    assert cells[0] == "Savings Balance"
    assert cells[1] == "$4,210.55", "the value must be reachable as the cell adjacent to its label"


def test_unnamed_controls_are_still_perceived(
    driver: PlaywrightCdpDriver, base_app: _Server
) -> None:
    """The sub-account form's controls have no label, title, placeholder or id.

    They must still appear in the snapshot -- a resolver cannot anchor to something it cannot see.
    """
    sign_in(driver, base_app.url)
    driver.page.goto(f"{base_app.url}/member/12345/new-subaccount")
    snapshot = driver.observe()

    combobox = next((n for n in snapshot.nodes if n.role == "combobox"), None)
    assert combobox is not None, "the account-type control is missing from the snapshot"
    assert not combobox.name, "this control is supposed to have no accessible name"

    row = snapshot.nearest_ancestor(combobox, "row")
    assert row is not None
    labels = [n.name for n in snapshot.descendants(row) if n.role == "cell" and n.name]
    assert "Account Type" in labels, "the only handle on this control is its label cell"


# -------------------------------------------------------------- determinism


def test_snapshots_of_an_unchanged_page_are_identical(
    driver: PlaywrightCdpDriver, base_app: _Server
) -> None:
    """Stable node ids are the precondition for everything downstream: the determinism check
    compares traces, drift detection compares fingerprints, and handoff reconciliation diffs
    snapshots. An id derived from a counter would break all three while looking fine."""
    sign_in(driver, base_app.url)
    driver.page.goto(f"{base_app.url}/search")

    first = driver.observe()
    second = driver.observe()

    assert [n.node_id for n in first.nodes] == [n.node_id for n in second.nodes]
    assert [n.fingerprint for n in first.nodes] == [n.fingerprint for n in second.nodes]


# --------------------------------------------------------------- variant B


def test_variant_b_keeps_semantics_but_breaks_selectors(
    driver: PlaywrightCdpDriver, variant_b_app: _Server
) -> None:
    """Case A of the cross-tenant story, measured on a real browser.

    Variant B changes the form field name and CSS class; the accessible name survives. So a cached
    `hints.css` from a base recording is worthless here, and `semantic_exact` still resolves. That
    is the falsifiable form of "this system does not depend on CSS selectors".
    """
    sign_in(driver, variant_b_app.url)
    driver.page.goto(f"{variant_b_app.url}/search")
    snapshot = driver.observe()

    textbox = next(n for n in snapshot.nodes if n.role == "textbox" and n.name == "Member Number")

    # Semantics survive the tenant's restyle...
    assert textbox.name == "Member Number"
    # ...while every selector a recording could have cached does not.
    assert textbox.hints.get("css") == 'input[name="member_num"]'
    assert textbox.hints.get("css") != 'input[name="memno"]'


def test_variant_b_rebranding_changes_the_anchor_text(
    driver: PlaywrightCdpDriver, variant_b_app: _Server
) -> None:
    """Case B: rebranding defeats structural anchoring too, because the anchor text *is* the label.

    This is the fact that justifies tenant overlays rather than a cleverer resolver. Guessing that
    "Savings Bal." means "Savings Balance" is how an automation ends up reading the wrong row.
    """
    sign_in(driver, variant_b_app.url)
    driver.page.goto(f"{variant_b_app.url}/member/12345")
    snapshot = driver.observe()

    labels = {n.name for n in snapshot.nodes if n.role == "cell"}
    assert "Savings Bal." in labels
    assert "Savings Balance" not in labels


# ------------------------------------------------------------ housekeeping


def test_roles_are_normalized_not_browser_specific(
    driver: PlaywrightCdpDriver, base_app: _Server
) -> None:
    """Chromium's vocabulary must not leak upward, or every artifact would silently encode which
    surface it was recorded against."""
    sign_in(driver, base_app.url)
    driver.page.goto(f"{base_app.url}/member/12345")
    snapshot = driver.observe()

    roles = {node.role for node in snapshot.nodes}
    assert "LayoutTableCell" not in roles
    assert "RootWebArea" not in roles
    assert "cell" in roles
    assert all(role == normalize_role(role) for role in roles)


def test_session_info_declares_capabilities(driver: PlaywrightCdpDriver) -> None:
    """A capability's `surface.driver_capabilities` is checked against this, so a mismatch is
    caught before a run starts rather than mid-flow."""
    info = driver.session_info()
    assert info.driver == "playwright_cdp"
    assert "semantic_tree" in info.capabilities
