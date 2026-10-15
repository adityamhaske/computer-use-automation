"""An operator's raw click is classified by what it lands on.

The console sends the operator's gestures as `raw_input` -- coordinates and keys, not targets.
Policy classified them with no target at all, so every click came out `elevated` and was allowed,
including one on "Transfer Funds": the HUMAN profile's `irreversible: require_confirmation` could
not be reached from the only input path a human has. The engine-level tests in
`tests/invariants/test_human_control_safety.py` hand policy a target directly, which is why they
never saw it. These click real coordinates in a real browser, on a control inside a content frame,
because frame offsets are exactly where a hit test goes wrong.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cua.domain.action import ActionRisk, RawInput, RawInputKind
from cua.domain.actor import Actor
from cua.evidence.bus import EvidenceBus
from cua.policy.config import parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.redact import Redactor
from cua.runtime.dispatcher import Dispatcher, DispatchOutcome, DispatchStatus
from cua.surfaces.playwright_cdp.driver import PlaywrightCdpDriver
from cua.targeting.resolver import TargetResolver
from tests.integration.test_frame_navigation import Page, _serve

pytestmark = [pytest.mark.browser, pytest.mark.slow]

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"
DONE = "FUNDS-MOVED"


@pytest.fixture(scope="module")
def app() -> Iterator[tuple[str, str]]:
    pages: dict[str, Page] = {
        "/": (
            200,
            {},
            b'<html><head><title>shell</title></head><frameset cols="200,*">'
            b'<frame name="nav" src="/nav"><frame name="content" src="/content"></frameset></html>',
        ),
        "/nav": (200, {}, b"<html><body>nav</body></html>"),
        "/content": (
            200,
            {},
            (
                "<html><body style='margin:0'>"
                "<p style='position:absolute;left:20px;top:20px'>Account overview</p>"
                "<button style='position:absolute;left:300px;top:200px;width:160px;height:40px' "
                f"onclick=\"document.body.insertAdjacentText('beforeend', '{DONE}')\">"
                "Transfer Funds</button></body></html>"
            ).encode(),
        ),
    }
    port, server = _serve(pages)
    try:
        yield f"http://127.0.0.1:{port}/", f"127.0.0.1:{port}"
    finally:
        server.shutdown()


@pytest.fixture
def rig(app: tuple[str, str], tmp_path: Path) -> Iterator[tuple[Dispatcher, PlaywrightCdpDriver]]:
    entry, host = app
    config = parse_policy(POLICY.read_text())
    config = config.model_copy(
        update={"allowlist": config.allowlist.model_copy(update={"domains": (host,)})}
    )
    driver = PlaywrightCdpDriver(headless=True, session_id="raw")
    dispatcher = Dispatcher(
        driver=driver,
        policy=PolicyEngine(config),
        resolver=TargetResolver(),
        evidence=EvidenceBus(tmp_path, Redactor(config.redaction), run_id="raw"),
    )
    try:
        dispatcher.open_entrypoint(entry, session_id="raw")
        yield dispatcher, driver
    finally:
        driver.close()


def _content(driver: PlaywrightCdpDriver) -> Any:
    return next(f for f in driver.page.frames if f.name == "content")


def _button_centre(driver: PlaywrightCdpDriver) -> tuple[int, int]:
    """Where the button is on the page, measured independently of the code under test."""
    box = _content(driver).locator("button").bounding_box()
    assert box is not None
    return int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)


def _human(dispatcher: Dispatcher, action: RawInput, *, confirmed: bool = False) -> DispatchOutcome:
    return dispatcher.execute(
        action,
        snapshot=dispatcher.observe(),
        actor=Actor.HUMAN,
        session_id="raw",
        lease_epoch=1,
        confirmed=confirmed,
    )


def _moved(driver: PlaywrightCdpDriver) -> bool:
    # Rendered text, not `content()`: the marker also sits in the button's `onclick` source.
    return DONE in _content(driver).evaluate("document.body.innerText")


def test_a_raw_click_on_an_irreversible_control_needs_confirmation(rig) -> None:
    dispatcher, driver = rig
    x, y = _button_centre(driver)

    outcome = _human(dispatcher, RawInput(kind=RawInputKind.MOUSE_CLICK, x=x, y=y))

    assert outcome.status is DispatchStatus.NEEDS_CONFIRMATION, outcome.message
    assert outcome.decision is not None
    assert outcome.decision.risk.tier is ActionRisk.IRREVERSIBLE
    assert "Transfer Funds" in outcome.message
    assert not _moved(driver), "nothing may reach the page before the operator confirms"


def test_a_confirmed_raw_click_on_an_irreversible_control_goes_through(rig) -> None:
    dispatcher, driver = rig
    x, y = _button_centre(driver)

    outcome = _human(dispatcher, RawInput(kind=RawInputKind.MOUSE_CLICK, x=x, y=y), confirmed=True)

    assert outcome.status is DispatchStatus.OK, outcome.message
    assert _moved(driver)


def test_a_raw_click_on_nothing_dangerous_is_allowed(rig) -> None:
    dispatcher, driver = rig

    outcome = _human(dispatcher, RawInput(kind=RawInputKind.MOUSE_CLICK, x=230, y=30))

    assert outcome.status is DispatchStatus.OK, outcome.message
    assert outcome.decision is not None
    assert outcome.decision.risk.tier is ActionRisk.ELEVATED
    assert not _moved(driver)


def test_pressing_enter_on_a_focused_irreversible_control_needs_confirmation(rig) -> None:
    """A keyboard activates whatever has focus, so the key is classified by the focused control."""
    dispatcher, driver = rig
    _content(driver).locator("button").focus()

    outcome = _human(dispatcher, RawInput(kind=RawInputKind.KEY, key="Enter"))

    assert outcome.status is DispatchStatus.NEEDS_CONFIRMATION, outcome.message
    assert not _moved(driver)
