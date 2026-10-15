"""The allowlist governs every frame, not just the top-level document.

The applications this project targets are framesets: the shell never navigates, and every click that
goes anywhere navigates a *content frame*. Judging "did this navigate, and where to?" by the main
document's URL meant a link in the content frame could take the session to any host at all and the
dispatch still came back `ok`. Found by driving a real Chromium at exactly that page, which is why
these tests do the same rather than trusting a fake driver's `navigated` flag.
"""

from __future__ import annotations

import http.server
import socket
import socketserver
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from cua.domain.action import Click
from cua.domain.actor import Actor
from cua.domain.result import FailureCode
from cua.domain.target import NameMatch, TargetDescriptor
from cua.evidence.bus import EvidenceBus
from cua.policy.config import parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.redact import Redactor
from cua.runtime.dispatcher import Dispatcher, DispatchOutcome, DispatchStatus
from cua.surfaces.playwright_cdp.driver import PlaywrightCdpDriver
from cua.targeting.resolver import TargetResolver

pytestmark = [pytest.mark.browser, pytest.mark.slow]

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"
OFF_LIST_MARKER = "OFF-ALLOWLIST PAGE"

Page = tuple[int, dict[str, str], bytes]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _serve(pages: dict[str, Page]) -> tuple[int, socketserver.TCPServer]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, headers, body = pages.get(
                self.path, (200, {}, f"<html><body>{OFF_LIST_MARKER}</body></html>".encode())
            )
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return None

    port = _free_port()
    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return port, server


@pytest.fixture(scope="module")
def hosts() -> Iterator[tuple[str, str]]:
    """An allowlisted frameset app, and a host outside the allowlist it links to."""
    off_port, off_server = _serve({})
    off_list = f"http://localhost:{off_port}/collect"
    app_pages: dict[str, Page] = {
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
                f'<html><body><a href="{off_list}">Continue</a>'
                '<a href="/next">Next page</a><a href="/redirect">Redirected</a></body></html>'
            ).encode(),
        ),
        "/next": (200, {}, b"<html><body>Still inside</body></html>"),
        "/redirect": (302, {"Location": off_list}, b""),
    }
    app_port, app_server = _serve(app_pages)
    try:
        yield f"http://127.0.0.1:{app_port}/", f"127.0.0.1:{app_port}"
    finally:
        app_server.shutdown()
        off_server.shutdown()


@pytest.fixture
def rig(hosts: tuple[str, str], tmp_path: Path) -> Iterator[tuple[Dispatcher, PlaywrightCdpDriver]]:
    entry, host = hosts
    config = parse_policy(POLICY.read_text())
    config = config.model_copy(
        update={
            "allowlist": config.allowlist.model_copy(
                update={"domains": (host,), "url_patterns": ()}
            )
        }
    )
    driver = PlaywrightCdpDriver(headless=True, session_id="frames")
    dispatcher = Dispatcher(
        driver=driver,
        policy=PolicyEngine(config),
        resolver=TargetResolver(),
        evidence=EvidenceBus(tmp_path, Redactor(config.redaction), run_id="frames"),
    )
    try:
        dispatcher.open_entrypoint(entry, session_id="frames")
        yield dispatcher, driver
    finally:
        driver.close()


def _click(dispatcher: Dispatcher, name: str) -> DispatchOutcome:
    return dispatcher.execute(
        Click(target=TargetDescriptor(role="link", name=NameMatch(value=name))),
        snapshot=dispatcher.observe(),
        actor=Actor.AUTOMATION,
        session_id="frames",
        lease_epoch=1,
    )


def _content_frame_text(driver: PlaywrightCdpDriver) -> str:
    driver.page.wait_for_timeout(300)  # let any navigation the click started settle first
    frame = next(f for f in driver.page.frames if f.name == "content")
    return frame.content()


def test_a_link_in_a_content_frame_cannot_take_the_session_off_the_allowlist(rig) -> None:
    dispatcher, driver = rig

    outcome = _click(dispatcher, "Continue")

    assert outcome.status is DispatchStatus.DENIED, outcome.message
    assert outcome.failure_code is FailureCode.NAVIGATION_BLOCKED
    assert "localhost" in outcome.message
    # Refused, not merely reported: the off-list document never loaded into the frame.
    assert OFF_LIST_MARKER not in _content_frame_text(driver)


def test_a_redirect_off_the_allowlist_from_a_frame_is_refused(rig) -> None:
    """The first hop is on the allowlist; only the `Location` it answers with is not."""
    dispatcher, driver = rig

    outcome = _click(dispatcher, "Redirected")

    assert outcome.status is DispatchStatus.DENIED, outcome.message
    assert outcome.failure_code is FailureCode.NAVIGATION_BLOCKED
    assert OFF_LIST_MARKER not in _content_frame_text(driver)


def test_a_frame_navigation_inside_the_allowlist_still_works(rig) -> None:
    dispatcher, driver = rig

    outcome = _click(dispatcher, "Next page")

    assert outcome.status is DispatchStatus.OK, outcome.message
    assert "Still inside" in _content_frame_text(driver)
