"""The console's live channel: confirmation, refusals, and who may talk to it at all.

Driven with a stand-in broker rather than a browser. What is under test is the console's own wiring
-- which broker method a gesture reaches, what the operator is sent back, and which requests are
refused before reaching the broker at all -- none of which needs Chromium. The policy decisions the
broker's methods lead to are covered against a real browser in `test_human_raw_input_risk.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cua.domain.action import Action
from cua.hitl.console.server import ConsoleDeps, create_console
from cua.hitl.lease import ControlState, LeaseError
from cua.runtime.dispatcher import DispatchOutcome, DispatchStatus

SAME_ORIGIN = "http://testserver"
FOREIGN_ORIGIN = "https://evil.example.com"
CLICK = {"kind": "mouse_click", "x": 380, "y": 220}


@dataclass
class _Lease:
    state: ControlState = ControlState.HUMAN_CONTROL


@dataclass
class _Broker:
    """Records which door a gesture came through, and answers as the dispatcher would."""

    answer: DispatchStatus = DispatchStatus.OK
    raise_lease_error: bool = False
    calls: list[tuple[str, Action]] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    lease: _Lease = field(default_factory=_Lease)

    def human_action(self, action: Action) -> DispatchOutcome:
        if self.raise_lease_error:
            raise LeaseError("no operator holds session sess-x (state: automation)")
        self.calls.append(("human_action", action))
        return DispatchOutcome(status=self.answer, message="irreversible ('Transfer Funds')")

    def confirm_action(self, action: Action) -> DispatchOutcome:
        self.calls.append(("confirm_action", action))
        return DispatchOutcome(status=DispatchStatus.OK)

    def claim(self, intervention_id: str, operator: str) -> int:
        self.claims.append(intervention_id)
        return 3


class _Driver:
    def screenshot(self, *, redact: tuple = ()) -> bytes:
        return b"\x89PNG"


@pytest.fixture
def broker() -> _Broker:
    return _Broker()


@pytest.fixture
def client(broker: _Broker) -> TestClient:
    return TestClient(create_console(ConsoleDeps(broker=broker, driver=_Driver())))  # type: ignore[arg-type]


def _gesture(client: TestClient, message: dict[str, Any], **headers: str) -> dict[str, Any]:
    with client.websocket_connect("/ws", headers=headers) as socket:
        assert socket.receive_json()["status"] == "connected"
        socket.send_json(message)
        reply: dict[str, Any] = socket.receive_json()
        return reply


# ---------------------------------------------------------------- confirmation


def test_a_gesture_that_needs_confirmation_is_handed_back_to_be_confirmed(client, broker) -> None:
    """The operator is told what they are about to do, and given the gesture to confirm -- policy's
    REQUIRE_CONFIRMATION is otherwise a dead end the console cannot get past."""
    broker.answer = DispatchStatus.NEEDS_CONFIRMATION

    reply = _gesture(client, CLICK)

    assert reply["status"] == "needs_confirmation"
    assert "Transfer Funds" in reply["message"]
    assert reply["gesture"] == CLICK
    assert [name for name, _ in broker.calls] == ["human_action"]


def test_a_confirmed_gesture_goes_through_the_confirmation_path(client, broker) -> None:
    reply = _gesture(client, {**CLICK, "confirm": True})

    assert reply["status"] == "ok"
    assert [name for name, _ in broker.calls] == ["confirm_action"]


@pytest.mark.parametrize("flag", ["true", 1, "yes"])
def test_only_a_literal_true_confirms(client, broker, flag: object) -> None:
    """A confirmation is a deliberate act; a truthy accident must not count as one."""
    _gesture(client, {**CLICK, "confirm": flag})

    assert [name for name, _ in broker.calls] == ["human_action"]


def test_a_gesture_without_the_lease_is_refused_not_a_dropped_connection(client, broker) -> None:
    """`LeaseError` escaped the handler and closed the socket with a server error, so an operator
    whose hold had lapsed saw the console go dead rather than being told why."""
    broker.raise_lease_error = True

    with client.websocket_connect("/ws") as socket:
        socket.receive_json()
        socket.send_json(CLICK)
        assert "no operator holds" in socket.receive_json()["error"]
        # Still open: the next gesture gets an answer too.
        socket.send_json(CLICK)
        assert "error" in socket.receive_json()


# ---------------------------------------------------------------------- origin


def test_a_foreign_page_cannot_open_the_live_channel(client) -> None:
    """Browsers do not apply the same-origin policy to WebSockets, and binding to 127.0.0.1 does
    not stop a page the operator has open from connecting to it. The Origin header is the check."""
    with pytest.raises(WebSocketDisconnect):
        _gesture(client, CLICK, origin=FOREIGN_ORIGIN)


def test_the_console_itself_may_open_the_live_channel(client) -> None:
    assert _gesture(client, CLICK, origin=SAME_ORIGIN)["status"] == "ok"


def test_a_client_that_sends_no_origin_is_not_a_browser_and_is_let_through(client) -> None:
    """Every browser sends Origin on a WebSocket and on a cross-site POST, so its absence means a
    non-browser client -- which a foreign web page cannot be."""
    assert _gesture(client, CLICK)["status"] == "ok"


def test_a_foreign_page_cannot_claim_an_intervention(client, broker) -> None:
    """A cross-site form POST needs no preflight, so without this any open tab could claim the
    session the operator is about to take."""
    response = client.post("/api/claim/int-1", headers={"origin": FOREIGN_ORIGIN})

    assert response.status_code == 403
    assert broker.claims == []


def test_the_console_itself_may_claim(client, broker) -> None:
    response = client.post("/api/claim/int-1", headers={"origin": SAME_ORIGIN})

    assert response.status_code == 200
    assert broker.claims == ["int-1"]


def test_reads_are_not_origin_checked(client) -> None:
    """Only state-changing requests are gated; a GET a foreign page can trigger changes nothing, and
    the browser will not let that page read the answer."""
    assert client.get("/", headers={"origin": FOREIGN_ORIGIN}).status_code == 200
