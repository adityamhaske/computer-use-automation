"""The mock back-office over a real socket.

`TestClient` calls the ASGI app in-process, which cannot see the things that only exist on the
wire: a real cookie jar following real redirects, the URL length a real HTTP parser tolerates, and
what a failed request looks like once uvicorn -- not the test harness -- has turned it into bytes.
These few tests are the ones that would pass in-process and still be wrong over HTTP.

Offline: a loopback server on an OS-assigned free port, no model, no network beyond 127.0.0.1.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from apps.mock_bank.server import SESSION_COOKIE, VALID_PW, VALID_USER, create_app
from apps.mock_bank.variants import BASE


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Server:
    def __init__(self, variant: str) -> None:
        self.port = _free_port()
        self._server = uvicorn.Server(
            uvicorn.Config(create_app(variant), host="127.0.0.1", port=self.port, log_level="error")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> _Server:
        self._thread.start()
        # Wait on the server's own readiness flag, with a deadline; no fixed sleep.
        deadline = time.monotonic() + 10
        ready = threading.Event()
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("mock app did not start")
            ready.wait(0.02)
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


@pytest.fixture
def live() -> Iterator[_Server]:
    """A fresh server per test: fault state is process-local, so sharing one across tests would
    let an armed fault leak from one test into the next."""
    with _Server("base") as server:
        yield server


def _sign_in(client: httpx.Client, server: _Server) -> None:
    # Explicitly not followed, whatever the client's default: the sign-in is proven by the 303
    # itself.
    response = client.post(
        f"{server.url}/login",
        data={"user": VALID_USER, "pw": VALID_PW},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_a_timeout_mid_journey_lands_a_real_browser_on_the_login_screen_and_recovery_resumes(
    live: _Server,
) -> None:
    """Follow redirects with a genuine cookie jar: the expiry's `Set-Cookie: Max-Age=0` must
    actually evict the cookie, or the next request would still look signed in."""
    with httpx.Client(follow_redirects=True, timeout=10) as client:
        client.post(f"{live.url}/login", data={"user": VALID_USER, "pw": VALID_PW})
        assert "Member Search" in client.get(f"{live.url}/search").text
        client.post(f"{live.url}/_control/arm", json={"fault": "session_timeout", "count": 1})

        expired = client.get(f"{live.url}/member/12345")

        assert expired.url.path == "/login"
        assert "Sign On" in expired.text
        assert client.cookies.get(SESSION_COOKIE) is None

        client.post(f"{live.url}/login", data={"user": VALID_USER, "pw": VALID_PW})
        resumed = client.get(f"{live.url}/member/12345")
        assert resumed.url.path == "/member/12345"
        assert "Ada Lovelace" in resumed.text


def test_a_search_follows_its_redirect_to_the_record_over_the_wire(live: _Server) -> None:
    with httpx.Client(follow_redirects=True, timeout=10) as client:
        _sign_in(client, live)

        response = client.post(f"{live.url}/search", data={BASE.field_member_no: "67890"})

        assert response.url.path == "/member/67890"
        assert "Grace Hopper" in response.text
        assert [r.status_code for r in response.history] == [303]


@pytest.mark.parametrize("length", [2_000, 10_000, 30_000])
def test_a_very_long_member_id_in_the_url_is_a_plain_no_such_member(
    live: _Server, length: int
) -> None:
    """The HTTP parser, not the app, is what first meets a huge path. Up to a size a browser could
    send, it must still end as the business outcome rather than a transport error."""
    with httpx.Client(timeout=10) as client:
        client.post(f"{live.url}/login", data={"user": VALID_USER, "pw": VALID_PW})

        response = client.get(f"{live.url}/member/{'7' * length}")

        assert response.status_code == 200
        assert "No records found" in response.text


def test_a_rejected_arm_returns_a_bare_error_over_the_wire_with_no_traceback(
    live: _Server,
) -> None:
    """Under uvicorn an unhandled error becomes a generic 500 body: the traceback belongs in the
    server log, never in what the caller (here, an out-of-band harness) receives."""
    with httpx.Client(timeout=10) as client:
        response = client.post(f"{live.url}/_control/arm", json={"fault": "no-such-fault"})

        assert response.status_code >= 400
        for needle in ("Traceback", "faults.py", 'File "', "ValueError"):
            assert needle not in response.text
    # ...and the server survived it: a new request is answered normally. A NEW connection, not the
    # same client: uvicorn closes the connection after an unhandled application error, and whether
    # a request reused on that keep-alive connection sees the reset depends on the platform (it
    # passed on macOS and failed on Linux CI with "Connection reset by peer").
    with httpx.Client(timeout=10) as fresh:
        assert fresh.get(f"{live.url}/_control/state").json()["armed"] == {}


def test_two_clients_share_one_fault_but_not_one_session(live: _Server) -> None:
    """Faults are app-wide (one armed 502 is spent by whichever request arrives first); sessions
    are per-client. A harness and a browser on the same app see exactly that split."""
    with httpx.Client(timeout=10) as first, httpx.Client(timeout=10) as second:
        _sign_in(first, live)
        assert second.get(f"{live.url}/search", follow_redirects=False).status_code == 303
        first.post(f"{live.url}/_control/arm", json={"fault": "transient_load", "count": 1})

        spent_by_second = second.get(f"{live.url}/search", follow_redirects=False)
        first_after = first.get(f"{live.url}/search")

        assert spent_by_second.status_code == 502
        assert first_after.status_code == 200


def test_the_static_stylesheet_is_served_with_the_right_type_and_is_stable(live: _Server) -> None:
    with httpx.Client(timeout=10) as client:
        first = client.get(f"{live.url}/static/base.css")
        second = client.get(f"{live.url}/static/base.css")

        assert first.status_code == 200
        assert first.headers["content-type"].startswith("text/css")
        assert first.content == second.content
        assert client.get(f"{live.url}/static/%2e%2e/server.py").status_code == 404
