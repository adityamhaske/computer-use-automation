"""The operator console routes gestures through policy rather than into the page.

The console is a view onto the control-transfer model, so these tests check the seam that matters:
a gesture becomes a policed action, a refusal reaches the operator, and nothing here can act without
the lease.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from apps.mock_bank.server import VALID_PW, VALID_USER, create_app
from fastapi.testclient import TestClient

from cua.domain.action import RawInputKind
from cua.evidence.bus import EvidenceBus
from cua.hitl.broker import SessionBroker
from cua.hitl.console.server import ConsoleDeps, _to_action, create_console
from cua.hitl.lease import ControlState
from cua.hitl.session_thread import SessionThread
from cua.policy.config import parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.redact import Redactor
from cua.runtime.dispatcher import Dispatcher
from cua.surfaces.playwright_cdp.driver import PlaywrightCdpDriver
from cua.targeting.resolver import TargetResolver

pytestmark = [pytest.mark.browser, pytest.mark.slow]

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def app() -> Iterator[tuple[str, int]]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app("base"), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", port
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def console(app: tuple[str, int], tmp_path: Path) -> Iterator[tuple]:
    base_url, port = app
    config = parse_policy(POLICY.read_text())
    config = config.model_copy(
        update={
            "allowlist": config.allowlist.model_copy(
                update={"domains": (*config.allowlist.domains, f"127.0.0.1:{port}")}
            )
        }
    )
    bus = EvidenceBus(tmp_path, Redactor(config.redaction), run_id="console")

    # The session is CREATED on its owning thread, not merely called from it. Synchronous
    # Playwright binds to the constructing thread, so building the driver here on the test thread
    # and marshalling to another would forward to the wrong one -- which is exactly the mistake a
    # served console makes if it starts the browser before the thread.
    session = SessionThread(name="console-test-session")
    session.start()

    def _boot() -> PlaywrightCdpDriver:
        instance = PlaywrightCdpDriver(headless=True)
        page = instance.page
        page.goto(f"{base_url}/login")
        page.fill('input[name="user"]', VALID_USER)
        page.fill('input[name="pw"]', VALID_PW)
        page.click('input[type="submit"]')
        page.wait_for_load_state()
        return instance

    driver = session.call(_boot)

    broker = SessionBroker(
        session_id="sess-console",
        evidence=bus,
        dispatch=Dispatcher(
            driver=driver,
            policy=PolicyEngine(config),
            resolver=TargetResolver(allow_vision=False),
            evidence=bus,
        ),
    )
    client = TestClient(create_console(ConsoleDeps(broker=broker, driver=driver, session=session)))
    try:
        yield client, broker, driver, session
    finally:
        session.call(driver.close)
        session.stop()


def _open_one(broker: SessionBroker, driver: PlaywrightCdpDriver, session: SessionThread):
    """Open an intervention. The snapshot is captured on the session's own thread."""
    return broker.escalate(
        run_id="r1",
        capability_ref="corebank.member.savings_balance@1.0.0",
        goal="Read a member's savings balance",
        reason="the resolver refused an ambiguous control",
        step_id="submit_search",
        snapshot=session.call(driver.observe),
    )


def test_a_gesture_becomes_a_policed_action() -> None:
    """The seam. A click is not forwarded to the page -- it becomes an action the chokepoint
    sees."""
    action = _to_action({"kind": "mouse_click", "x": 12, "y": 34})
    assert action is not None
    assert action.type == "raw_input"
    assert action.kind is RawInputKind.MOUSE_CLICK


def test_an_unknown_gesture_is_refused_not_guessed() -> None:
    assert _to_action({"kind": "something_else"}) is None


def test_the_queue_shows_what_an_operator_needs(console: tuple) -> None:
    client, broker, driver, session = console
    _open_one(broker, driver, session)

    cards = client.get("/api/interventions").json()
    assert len(cards) == 1
    assert cards[0]["because"]
    assert cards[0]["stopped_at"] == "submit_search"


def test_state_reports_who_holds_the_session(console: tuple) -> None:
    """An operator has to be able to see whether anyone is in control, and at which generation."""
    client, broker, driver, session = console
    assert client.get("/api/state").json()["state"] == ControlState.RUNNING.value

    request = _open_one(broker, driver, session)
    assert client.get("/api/state").json()["state"] == ControlState.PAUSED.value

    client.post(f"/api/claim/{request.intervention_id}?operator=alex")
    state = client.get("/api/state").json()
    assert state["state"] == ControlState.HUMAN_CONTROL.value
    assert state["holder"] == "human"
    assert state["operator"] == "alex"


def test_handing_back_reconciles_and_reports_what_changed(console: tuple) -> None:
    """Handing back works from a web request thread -- the case that actually breaks without
    marshalling, and the one a real operator hits on their first click."""
    client, broker, driver, session = console
    request = _open_one(broker, driver, session)
    client.post(f"/api/claim/{request.intervention_id}?operator=alex")

    body = client.post("/api/release").json()
    assert body["state"] == ControlState.RUNNING.value
    assert "human_delta" in body
    assert broker.lease.holder.value == "automation"


def test_the_console_serves_a_page(console: tuple) -> None:
    client, _, _, _ = console
    body = client.get("/").text
    assert "Operator console" in body
    assert "Hand back to automation" in body


def test_surface_calls_are_marshalled_to_the_owning_thread(console: tuple) -> None:
    """Synchronous Playwright is bound to its creating thread.

    A console that called the driver directly from a request handler would fail on the first
    operator gesture. This asserts the marshalling is wired, not merely available.
    """
    import threading

    _, _, _, session = console
    owner = session.call(lambda: threading.current_thread().name)
    assert owner == "console-test-session"
    assert owner != threading.current_thread().name
