"""Who may see what on the mock back-office: sign-on, sessions, routing and the launcher.

The fixture has no real auth system (AGENTS.md scope discipline), so the claims here are the
small ones the rest of the suite depends on: nothing behind the sign-on is reachable without a
session, a failed sign-on says nothing it should not, a session can be dropped and re-established,
and every route answers only the verbs it declares.

Offline: `fastapi.testclient` only; `main()` is exercised with `uvicorn.run` replaced, so nothing
binds a port.
"""

from __future__ import annotations

import sys
from html.parser import HTMLParser
from typing import Any

import pytest
from apps.mock_bank import server as server_module
from apps.mock_bank.server import SESSION_COOKIE, VALID_PW, VALID_USER, create_app
from apps.mock_bank.variants import BASE, VARIANT_B
from fastapi.testclient import TestClient

CANARY_PASSWORD = "S3cr3t-Canary-9f2c"
CANARY_USER = "canary-operator-7d1e"


def _client(variant: str = "base", *, signed_in: bool = False) -> TestClient:
    client = TestClient(create_app(variant), follow_redirects=False)
    if signed_in:
        response = client.post("/login", data={"user": VALID_USER, "pw": VALID_PW})
        assert response.status_code == 303
    return client


# --------------------------------------------------- unauthenticated access

FORM = {BASE.field_member_no: "12345"}
SUBMIT = {BASE.field_acct_type: "savings", BASE.field_deposit: "100.00"}

# Every route that sits behind the sign-on.
BEHIND_SIGN_ON = [
    pytest.param("get", "/search", {}, id="GET-search"),
    pytest.param("post", "/search", {"data": FORM}, id="POST-search"),
    pytest.param("get", "/member/12345", {}, id="GET-member"),
    pytest.param("get", "/member/99999", {}, id="GET-absent-member"),
    pytest.param("get", "/member/55555", {}, id="GET-restricted-member"),
    pytest.param("get", "/member/12345/new-subaccount", {}, id="GET-subaccount-form"),
    pytest.param("post", "/member/12345/new-subaccount", {"data": SUBMIT}, id="POST-subaccount"),
    pytest.param("get", "/reports", {}, id="GET-reports"),
    pytest.param("get", "/admin", {}, id="GET-admin"),
]


@pytest.mark.parametrize(("method", "path", "kwargs"), BEHIND_SIGN_ON)
@pytest.mark.parametrize("variant", ["base", "variant_b"])
def test_every_route_behind_the_sign_on_redirects_an_unauthenticated_caller_to_login(
    variant: str, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    response = getattr(_client(variant), method)(path, **kwargs)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert response.text == "" or "Ada Lovelace" not in response.text


def test_an_unauthenticated_caller_cannot_tell_a_real_member_from_an_absent_one() -> None:
    """If the redirect differed by member, the sign-on would be an oracle for which ids exist.
    Found, absent and restricted must all look exactly alike from outside."""
    client = _client()

    answers = {
        member_id: client.get(f"/member/{member_id}") for member_id in ("12345", "99999", "55555")
    }
    posted = {
        member_id: client.post("/search", data={BASE.field_member_no: member_id})
        for member_id in ("12345", "99999", "55555")
    }

    for group in (answers, posted):
        shapes = {(r.status_code, r.headers["location"], r.text) for r in group.values()}
        assert len(shapes) == 1


OPEN_ROUTES = [
    pytest.param("/", id="frameset"),
    pytest.param("/nav", id="nav"),
    pytest.param("/login", id="login"),
    pytest.param("/static/base.css", id="static-css"),
    pytest.param("/static/variant_b.css", id="static-css-b"),
    pytest.param("/static/spacer.gif", id="static-gif"),
]


@pytest.mark.parametrize("path", OPEN_ROUTES)
def test_the_pages_a_signed_out_browser_needs_are_reachable_without_a_session(path: str) -> None:
    """Sign-on, its frameset and its assets must load signed out, or nobody could ever sign on."""
    assert _client().get(path).status_code == 200


def test_the_signed_out_login_page_shows_no_identity_and_no_sign_off_link() -> None:
    body = _client().get("/login").text

    assert "Teller:" not in body
    assert "Sign Off" not in body


# ----------------------------------------------------- method not allowed

ROUTE_VERBS = {
    "/": {"GET"},
    "/nav": {"GET"},
    "/login": {"GET", "POST"},
    "/logout": {"GET"},
    "/search": {"GET", "POST"},
    "/member/12345": {"GET"},
    "/member/12345/new-subaccount": {"GET", "POST"},
    "/reports": {"GET"},
    "/admin": {"GET"},
    "/_control/arm": {"POST"},
    "/_control/reset": {"POST"},
    "/_control/state": {"GET"},
}
_ALL_VERBS = ("GET", "POST", "PUT", "PATCH", "DELETE")
WRONG_VERB = [
    pytest.param(path, verb, id=f"{verb}-{path}")
    for path, allowed in ROUTE_VERBS.items()
    for verb in _ALL_VERBS
    if verb not in allowed
]


@pytest.mark.parametrize(("path", "verb"), WRONG_VERB)
def test_a_route_refuses_every_verb_it_does_not_declare(path: str, verb: str) -> None:
    """405 with an `Allow` header that names only verbs the route really takes. A route that
    quietly answered a PUT or DELETE would be a mutation path nobody reviewed.

    The header is checked for soundness, not completeness: where GET and POST are registered as
    two routes on one path the framework reports just the first, which is not this app's doing.
    """
    client = _client(signed_in=True)

    response = client.request(verb, path)

    assert response.status_code == 405
    advertised = {v.strip() for v in response.headers["allow"].split(",")}
    assert advertised, "a 405 must say what would have worked"
    assert advertised <= ROUTE_VERBS[path] | {"HEAD"}


def test_method_not_allowed_is_decided_before_the_sign_on() -> None:
    """Routing precedes the guard: an unauthenticated DELETE is a 405, not a leak of the session
    redirect, and certainly not a deletion."""
    assert _client().delete("/member/12345").status_code == 405


def test_an_unknown_route_is_a_404_not_a_login_redirect_and_never_a_page() -> None:
    signed_out = _client().get("/definitely/not/here")
    signed_in = _client(signed_in=True).get("/definitely/not/here")

    assert signed_out.status_code == signed_in.status_code == 404


# --------------------------------------------------------------- sign-on

# (operator, password, accepted)
CREDENTIALS = [
    pytest.param(VALID_USER, VALID_PW, True, id="the-fixture-credentials"),
    pytest.param(f"  {VALID_USER}  ", VALID_PW, True, id="operator-id-is-trimmed"),
    pytest.param(f"\t{VALID_USER}\n", VALID_PW, True, id="operator-id-tab-and-newline-trimmed"),
    pytest.param(VALID_USER, "wrong", False, id="wrong-password"),
    pytest.param(VALID_USER, "", False, id="empty-password"),
    pytest.param("", VALID_PW, False, id="empty-operator"),
    pytest.param("", "", False, id="both-empty"),
    pytest.param("   ", "   ", False, id="both-blank"),
    pytest.param(VALID_USER.upper(), VALID_PW, False, id="operator-id-is-case-sensitive"),
    pytest.param(VALID_USER, VALID_PW.upper(), False, id="password-is-case-sensitive"),
    pytest.param(VALID_USER, f" {VALID_PW}", False, id="password-is-never-trimmed-leading"),
    pytest.param(VALID_USER, f"{VALID_PW} ", False, id="password-is-never-trimmed-trailing"),
    pytest.param("tеller01", VALID_PW, False, id="cyrillic-e-homoglyph-operator"),
    pytest.param(f"{VALID_USER}​", VALID_PW, False, id="zero-width-space-operator"),
    pytest.param(f"{VALID_USER}\x00", VALID_PW, False, id="nul-operator"),
    pytest.param(VALID_USER, f"{VALID_PW}​", False, id="zero-width-space-password"),
    pytest.param("teller０１", VALID_PW, False, id="fullwidth-digits-operator"),
    pytest.param("' OR '1'='1", "' OR '1'='1", False, id="sql-tautology"),
    pytest.param(f"{VALID_USER}'--", "x", False, id="sql-comment"),
    pytest.param("{{7*7}}", "{{7*7}}", False, id="template-expression"),
    pytest.param("../../etc/passwd", "x", False, id="path-traversal"),
    pytest.param("a" * 10_000, "b" * 10_000, False, id="ten-thousand-characters"),
    pytest.param("admin", "", False, id="admin-without-a-password"),
    pytest.param("root", "root", False, id="an-account-the-screen-does-not-list"),
]


@pytest.mark.parametrize(("operator", "password", "accepted"), CREDENTIALS)
def test_sign_on_accepts_only_the_advertised_credentials_exactly(
    operator: str, password: str, accepted: bool
) -> None:
    client = _client()

    response = client.post("/login", data={"user": operator, "pw": password})

    if accepted:
        assert (response.status_code, response.headers["location"]) == (303, "/search")
        assert client.get("/search").status_code == 200
    else:
        assert response.status_code == 200
        assert "Invalid operator ID or password." in response.text
        assert "set-cookie" not in response.headers
        assert client.get("/search").status_code == 303, "a failed sign-on must not open a session"


def test_a_failed_sign_on_echoes_neither_the_operator_id_nor_the_password() -> None:
    """The error page is a sink. Reflecting what was typed would put a mistyped real password on
    screen, in the page source, and in anything that snapshots it."""
    response = _client().post("/login", data={"user": CANARY_USER, "pw": CANARY_PASSWORD})

    assert response.status_code == 200
    assert CANARY_PASSWORD not in response.text
    assert CANARY_USER not in response.text
    assert CANARY_PASSWORD not in str(response.headers)


def test_a_failed_sign_on_does_not_reveal_whether_the_operator_exists() -> None:
    """A real operator with a bad password and an unknown operator must be indistinguishable."""
    known = _client().post("/login", data={"user": VALID_USER, "pw": "wrong"})
    unknown = _client().post("/login", data={"user": CANARY_USER, "pw": "wrong"})

    assert known.status_code == unknown.status_code == 200
    assert known.text == unknown.text


def test_sign_on_with_no_form_body_is_a_failed_sign_on_not_a_server_error() -> None:
    response = _client().post("/login")

    assert response.status_code == 200
    assert "Invalid operator ID or password." in response.text


def test_sign_on_ignores_a_redirect_target_in_the_query_string() -> None:
    """No `next=` handling: the only place sign-on sends you is /search. An honoured redirect
    parameter is the classic open redirect."""
    response = _client().post(
        "/login?next=https://evil.example/&redirect=//evil.example",
        data={"user": VALID_USER, "pw": VALID_PW},
    )

    assert response.headers["location"] == "/search"


def test_a_successful_sign_on_sets_one_http_only_cookie_and_no_secret() -> None:
    response = _client().post("/login", data={"user": VALID_USER, "pw": VALID_PW})

    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 1
    assert cookies[0].startswith(f"{SESSION_COOKIE}=")
    assert "httponly" in cookies[0].lower(), "script in the page must not be able to read it"
    assert VALID_PW not in cookies[0]
    assert VALID_PW not in response.headers["location"]


def test_signing_on_twice_is_harmless() -> None:
    client = _client(signed_in=True)

    second = client.post("/login", data={"user": VALID_USER, "pw": VALID_PW})

    assert second.status_code == 303
    assert client.get("/search").status_code == 200


class _Options(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.options: list[str] = []
        self._in = False
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "option":
            self._in = True
            self.options.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "option":
            self._in = False

    def handle_data(self, data: str) -> None:
        if self._in:
            self.options[-1] += data


@pytest.mark.parametrize("variant", ["base", "variant_b"])
def test_every_credential_the_sign_on_screen_advertises_actually_signs_on(variant: str) -> None:
    """The screen prints its demo credentials so a first-time reader is not stopped by a wall they
    cannot get past. If it advertises a pair that does not work, it is lying to exactly the person
    least able to tell."""
    page = _client(variant).get("/login").text
    advertised = [o.strip() for o in _Options(page).options]

    assert f"{VALID_USER} / {VALID_PW}" in advertised, "the fixture credentials are shown"
    assert len(advertised) >= 1
    for entry in advertised:
        operator, _, password = entry.partition(" / ")
        attempt = _client(variant).post("/login", data={"user": operator, "pw": password})
        assert attempt.status_code == 303, f"advertised credentials {entry!r} were refused"


# -------------------------------------------------------------- sessions


@pytest.mark.parametrize("cookie_value", ["", " ", "\t"])
def test_an_empty_or_blank_session_cookie_is_not_a_session(cookie_value: str) -> None:
    client = _client()

    response = client.get("/search", headers={"Cookie": f"{SESSION_COOKIE}={cookie_value}"})

    assert (response.status_code, response.headers["location"]) == (303, "/login")


def test_the_session_cookie_value_is_escaped_where_the_banner_prints_it() -> None:
    """The banner shows the session's operator, straight from the cookie. A client-controlled
    value must land as text: a forged cookie is one of the few inputs an attacker fully owns."""
    hostile = "<img src=x onerror=alert(1)>"

    response = _client().get("/search", headers={"Cookie": f"{SESSION_COOKIE}={hostile}"})

    assert response.status_code == 200
    assert "<img src=x" not in response.text
    assert "&lt;img src=x" in response.text


def test_the_banner_shows_the_signed_in_operator_and_a_sign_off_link() -> None:
    body = _client(signed_in=True).get("/search").text

    assert f"Teller: <b>{VALID_USER}</b>" in body
    assert 'href="/logout"' in body


def test_a_session_belongs_to_one_client_not_to_the_server() -> None:
    """Two browsers against one app: signing one on must not sign the other on. (Module-level
    'current user' state is the classic way a fixture leaks a login between tests.)"""
    app = create_app("base")
    first = TestClient(app, follow_redirects=False)
    second = TestClient(app, follow_redirects=False)

    first.post("/login", data={"user": VALID_USER, "pw": VALID_PW})

    assert first.get("/search").status_code == 200
    assert second.get("/search").status_code == 303


# ---------------------------------------------------------------- logout


def test_sign_off_clears_the_cookie_and_returns_to_the_frameset() -> None:
    client = _client(signed_in=True)

    response = client.get("/logout")

    assert (response.status_code, response.headers["location"]) == (303, "/")
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert not client.cookies.get(SESSION_COOKIE)
    assert client.get("/member/12345").headers["location"] == "/login"


def test_signing_off_twice_and_signing_off_when_signed_out_are_harmless() -> None:
    client = _client(signed_in=True)

    outcomes = [client.get("/logout").status_code for _ in range(3)]

    assert outcomes == [303, 303, 303]
    assert _client().get("/logout").status_code == 303


def test_sign_off_can_be_followed_by_a_fresh_sign_on() -> None:
    client = _client(signed_in=True)
    client.get("/logout")

    client.post("/login", data={"user": VALID_USER, "pw": VALID_PW})

    assert client.get("/member/12345").status_code == 200


# ---------------------------------------------- query strings and headers


def test_query_strings_are_never_reflected_into_a_page() -> None:
    """A reflected query parameter is an XSS sink nobody asked for. None of these pages has a
    reason to echo one, so a canary must not come back."""
    canary = "q-canary-<b>7f3a</b>"
    client = _client(signed_in=True)

    for path in ("/login", "/search", "/member/12345", "/member/99999", "/nav", "/"):
        body = client.get(path, params={"error": canary, "memno": canary, "next": canary}).text
        assert "q-canary" not in body, path


def test_html_pages_declare_a_charset_so_non_ascii_survives_the_trip() -> None:
    """Unicode member ids and account types are echoed; without an explicit charset a browser is
    free to mangle them."""
    for path in ("/login", "/nav", "/"):
        assert "charset=utf-8" in _client().get(path).headers["content-type"].lower(), path


# -------------------------------------------------------------- launcher


class _CapturedRun:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def __call__(self, app: Any, **kwargs: Any) -> None:
        self.calls.append((app, kwargs))


def test_the_launcher_binds_loopback_only_unless_told_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deliberately hostile fixture that holds fake credentials and an unauthenticated control
    surface must not listen on every interface by default."""
    run = _CapturedRun()
    monkeypatch.setattr(server_module.uvicorn, "run", run)
    monkeypatch.setattr(sys, "argv", ["mock_bank", "--port", "9"])

    server_module.main()

    [(app, kwargs)] = run.calls
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 9
    assert app.state.variant.key == "base"


def test_the_launcher_serves_the_requested_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _CapturedRun()
    monkeypatch.setattr(server_module.uvicorn, "run", run)
    monkeypatch.setattr(sys, "argv", ["mock_bank", "--variant", "variant_b", "--port", "9"])

    server_module.main()

    [(app, _)] = run.calls
    assert app.state.variant is VARIANT_B


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--variant", "variant_c"], id="unknown-variant"),
        pytest.param(["--variant", "BASE"], id="wrong-case-variant"),
        pytest.param(["--variant", ""], id="empty-variant"),
        pytest.param(["--port", "abc"], id="non-numeric-port"),
        pytest.param(["--port"], id="port-without-a-value"),
        pytest.param(["--no-such-flag"], id="unknown-flag"),
    ],
)
def test_the_launcher_refuses_bad_arguments_before_starting_a_server(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    run = _CapturedRun()
    monkeypatch.setattr(server_module.uvicorn, "run", run)
    monkeypatch.setattr(sys, "argv", ["mock_bank", *argv])

    with pytest.raises(SystemExit) as exit_info:
        server_module.main()

    assert exit_info.value.code == 2
    assert run.calls == [], "a bad flag must never get as far as serving"
