"""Fault injection on the mock back-office: every kind, every count, every ordering rule.

The fixture's whole job is to produce an exceptional state on cue and *only* on cue, so the
properties worth defending are the ones a downstream replay test silently leans on:

* a fault manifests exactly as many times as it was armed for, then stops (count 1 / N / -1);
* faults are consumed by the requests that can actually surface them, and by nothing else -- a
  fault eaten by the `/nav` frame load would make every replay test order-dependent;
* precedence between faults is fixed (a 502 cannot also be a login page);
* the control surface refuses garbage without arming half a fault.

Offline: `fastapi.testclient` only, no sockets, no model, no clock.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import assert_never
from urllib.parse import quote

import pytest
from apps.mock_bank.faults import Fault, FaultState
from apps.mock_bank.server import SESSION_COOKIE, VALID_PW, VALID_USER, create_app
from apps.mock_bank.variants import BASE, VARIANT_B
from fastapi.testclient import TestClient


def _client(variant: str = "base", *, signed_in: bool = True) -> TestClient:
    # raise_server_exceptions=False: the control-surface tests need to *observe* a 500 rather
    # than have the test client re-raise it, which is what an out-of-band caller would see.
    client = TestClient(create_app(variant), follow_redirects=False, raise_server_exceptions=False)
    if signed_in:
        _sign_in(client)
    return client


def _sign_in(client: TestClient) -> None:
    response = client.post("/login", data={"user": VALID_USER, "pw": VALID_PW})
    assert response.status_code == 303
    assert client.cookies.get(SESSION_COOKIE) == VALID_USER


def _arm(client: TestClient, fault: str, count: int | None = None) -> None:
    payload: dict[str, object] = {"fault": fault}
    if count is not None:
        payload["count"] = count
    assert client.post("/_control/arm", json=payload).status_code == 200


def _armed(client: TestClient) -> dict[str, int]:
    body = client.get("/_control/state").json()
    armed: dict[str, int] = body["armed"]
    return armed


def _manifests(client: TestClient, fault: Fault) -> bool:
    """Issue the one request that would surface `fault`, and say whether it did.

    Exhaustive on purpose (`assert_never`): a fifth Fault kind added without a way to observe it
    here must fail this file rather than quietly escape every parametrized test below.
    """
    submit = {BASE.field_acct_type: "savings", BASE.field_deposit: "100.00"}
    if fault is Fault.TRANSIENT_LOAD:
        return client.get("/search").status_code == 502
    if fault is Fault.SESSION_TIMEOUT:
        if not client.cookies.get(SESSION_COOKIE):
            _sign_in(client)  # a previous timeout dropped it; sign-on itself is never guarded
        response = client.get("/search")
        return response.status_code == 303 and response.headers["location"] == "/login"
    if fault is Fault.UNDECLARED_DIALOG:
        return "System Notice" in client.get("/search").text
    if fault is Fault.VALIDATION_ERROR:
        response = client.post("/member/12345/new-subaccount", data=submit)
        return "below the minimum" in response.text
    assert_never(fault)


ALL_FAULTS = list(Fault)


# ------------------------------------------------ every kind x every count


@pytest.mark.parametrize("fault", ALL_FAULTS)
@pytest.mark.parametrize("count", [1, 2, 5])
def test_a_fault_manifests_exactly_as_many_times_as_it_was_armed(fault: Fault, count: int) -> None:
    """Count N means N failures and then a clean request, for every kind.

    An off-by-one here (N-1 or N+1 failures) would make the 'recovered after one retry' and
    'exhausted after four' rows of the fault matrix measure the fixture instead of the system.
    """
    client = _client()
    _arm(client, fault.value, count)

    observed = [_manifests(client, fault) for _ in range(count + 2)]

    assert observed == [True] * count + [False, False]
    assert _armed(client) == {fault.value: 0}


@pytest.mark.parametrize("fault", ALL_FAULTS)
def test_an_indefinite_fault_never_clears_and_never_changes_its_count(fault: Fault) -> None:
    """-1 is how RECOVERY_EXHAUSTED is reachable at all: it must outlast any retry budget."""
    client = _client()
    _arm(client, fault.value, -1)

    assert all(_manifests(client, fault) for _ in range(25))
    assert _armed(client) == {fault.value: -1}


@pytest.mark.parametrize("fault", ALL_FAULTS)
def test_an_unarmed_fault_never_manifests(fault: Fault) -> None:
    """The control case every other test is measured against: a fresh app is fault-free."""
    client = _client()

    assert _manifests(client, fault) is False
    assert _armed(client) == {}


@pytest.mark.parametrize("fault", ALL_FAULTS)
def test_count_zero_disarms_a_fault(fault: Fault) -> None:
    """'Unarm' by count: arming with 0 after an indefinite arm must switch the fault off."""
    client = _client()
    _arm(client, fault.value, -1)
    assert _manifests(client, fault)

    _arm(client, fault.value, 0)

    assert _manifests(client, fault) is False
    assert _armed(client) == {fault.value: 0}


@pytest.mark.parametrize("fault", ALL_FAULTS)
def test_omitting_the_count_arms_exactly_one_trigger(fault: Fault) -> None:
    """The runbook's `{"fault": name}` form: a bare arm is a single deterministic failure."""
    client = _client()
    _arm(client, fault.value)  # no count key at all

    assert [_manifests(client, fault) for _ in range(3)] == [True, False, False]


@pytest.mark.parametrize("fault", ALL_FAULTS)
def test_rearming_overwrites_the_remaining_count_instead_of_adding_to_it(fault: Fault) -> None:
    """Arm 5 then arm 1 means one more failure, not six. Additive arming would make a harness
    that re-arms before every run accumulate failures it never asked for."""
    client = _client()
    _arm(client, fault.value, 5)

    _arm(client, fault.value, 1)

    assert _armed(client) == {fault.value: 1}
    assert [_manifests(client, fault) for _ in range(3)] == [True, False, False]


@pytest.mark.parametrize("fault", ALL_FAULTS)
def test_a_consumed_fault_can_be_armed_again(fault: Fault) -> None:
    client = _client()
    _arm(client, fault.value, 1)
    assert _manifests(client, fault)
    assert _manifests(client, fault) is False

    _arm(client, fault.value, 1)

    assert _manifests(client, fault)


@pytest.mark.parametrize("negative", [-2, -17, -1000])
def test_any_negative_count_is_unbounded_not_silently_disarmed(negative: int) -> None:
    """Only -1 is documented. The one outcome that would be a silent wrong answer is a negative
    count that quietly behaves as *zero*: the harness would believe it armed a fault that never
    fires, and the run would 'pass' having tested nothing."""
    client = _client()
    _arm(client, "transient_load", negative)

    assert [client.get("/search").status_code for _ in range(6)] == [502] * 6
    assert _armed(client) == {"transient_load": negative}


@pytest.mark.parametrize("huge", [10**9, 2**63, 10**30])
def test_a_huge_count_is_consumed_one_at_a_time_without_overflow_or_unbounded_behaviour(
    huge: int,
) -> None:
    """A large finite count must stay finite: decrement by exactly one per trigger. (Wrapping to
    a negative number would turn it into -1 semantics; clamping would make it fire once.)"""
    client = _client()
    _arm(client, "transient_load", huge)

    assert [client.get("/search").status_code for _ in range(3)] == [502] * 3
    assert _armed(client) == {"transient_load": huge - 3}


def test_different_fault_kinds_are_independent_of_each_other() -> None:
    """Arming a second kind must not reset, extend or consume the first, and each is spent only
    by the request that surfaces it."""
    client = _client()
    _arm(client, "transient_load", 1)
    _arm(client, "validation_error", 1)
    assert _armed(client) == {"transient_load": 1, "validation_error": 1}
    form = {BASE.field_acct_type: "savings", BASE.field_deposit: "1"}

    # The 502 is raised by guard() before the form handler runs, so the validation fault waits.
    assert client.post("/member/12345/new-subaccount", data=form).status_code == 502
    assert _armed(client) == {"transient_load": 0, "validation_error": 1}

    assert "below the minimum" in client.post("/member/12345/new-subaccount", data=form).text
    assert BASE.confirm_heading in client.post("/member/12345/new-subaccount", data=form).text


def test_reset_clears_every_armed_fault_and_is_idempotent() -> None:
    client = _client()
    for fault in ALL_FAULTS:
        _arm(client, fault.value, -1)

    first = client.post("/_control/reset")
    second = client.post("/_control/reset")

    assert first.json() == {"armed": {}}
    assert second.json() == {"armed": {}}
    assert _armed(client) == {}
    assert not any(_manifests(client, fault) for fault in ALL_FAULTS)


def test_each_app_instance_owns_its_own_fault_state() -> None:
    """Faults are process-local *per app*, not module-global. A global would leak an armed fault
    from one test's app into the next test's, which is the order-dependence this fixture exists
    to avoid."""
    first = _client()
    second = _client("variant_b")

    _arm(first, "transient_load", -1)

    assert _armed(second) == {}
    assert second.get("/search").status_code == 200
    assert first.get("/search").status_code == 502


@pytest.mark.parametrize(("variant", "key"), [("base", "base"), ("variant_b", "variant_b")])
def test_control_state_reports_the_variant_it_is_serving(variant: str, key: str) -> None:
    body = _client(variant).get("/_control/state").json()
    assert body == {"variant": key, "armed": {}}


# ---------------------------------------------------- consumption scope

# Every route that calls `guard()`. A fault must be surfaceable from each of them, because a
# capability can land on any of these and the fault matrix arms faults without knowing which.
GUARDED_REQUESTS = [
    pytest.param("get", "/search", {}, id="GET-search"),
    pytest.param("post", "/search", {"data": {BASE.field_member_no: "12345"}}, id="POST-search"),
    pytest.param("get", "/member/12345", {}, id="GET-member"),
    pytest.param("get", "/member/12345/new-subaccount", {}, id="GET-subaccount-form"),
    pytest.param(
        "post",
        "/member/12345/new-subaccount",
        {"data": {BASE.field_acct_type: "savings", BASE.field_deposit: "1"}},
        id="POST-subaccount",
    ),
    pytest.param("get", "/reports", {}, id="GET-reports"),
    pytest.param("get", "/admin", {}, id="GET-admin"),
]


@pytest.mark.parametrize(("method", "path", "kwargs"), GUARDED_REQUESTS)
def test_a_transient_fault_is_surfaced_by_every_guarded_route_and_consumed_once(
    method: str, path: str, kwargs: dict[str, object]
) -> None:
    client = _client()
    _arm(client, "transient_load", 1)

    blocked = getattr(client, method)(path, **kwargs)

    assert blocked.status_code == 502
    assert _armed(client) == {"transient_load": 0}
    assert getattr(client, method)(path, **kwargs).status_code != 502


# Routes that must NOT consume a fault. The frameset loads `/`, `/nav` and then the content
# frame; if the first two ate the fault the content frame -- the only page a capability sees --
# would never get it, and "arm one 502" would mean "arm zero" in a browser.
UNGUARDED_REQUESTS = [
    pytest.param("get", "/", id="frameset"),
    pytest.param("get", "/nav", id="nav-frame"),
    pytest.param("get", "/login", id="login-form"),
    pytest.param("get", "/static/base.css", id="static-css"),
    pytest.param("get", "/static/spacer.gif", id="static-gif"),
    pytest.param("get", "/_control/state", id="control-state"),
    pytest.param("get", "/no/such/route", id="unknown-route-404"),
    pytest.param("put", "/search", id="method-not-allowed"),
]


@pytest.mark.parametrize(("method", "path"), UNGUARDED_REQUESTS)
def test_unguarded_routes_neither_fail_nor_consume_an_armed_fault(method: str, path: str) -> None:
    client = _client()
    for fault in ALL_FAULTS:
        _arm(client, fault.value, 3)
    before = _armed(client)

    response = getattr(client, method)(path)

    assert response.status_code != 502
    assert _armed(client) == before


def test_logout_does_not_consume_an_armed_fault() -> None:
    client = _client()
    _arm(client, "transient_load", 2)

    assert client.get("/logout").status_code == 303

    assert _armed(client) == {"transient_load": 2}


def test_the_frameset_loads_cleanly_while_the_content_frame_takes_the_fault() -> None:
    """The browser sequence, end to end: `/`, `/nav`, then the content frame's `/search`."""
    client = _client()
    _arm(client, "transient_load", 1)

    assert client.get("/").status_code == 200
    assert client.get("/nav").status_code == 200
    assert client.get("/search").status_code == 502
    assert client.get("/search").status_code == 200


# ------------------------------------------------------------- precedence


def test_a_502_outranks_the_login_redirect_for_an_unauthenticated_request() -> None:
    """guard() documents this order: an unreachable server cannot also be showing a login page.
    Collapsing the two would make the fault matrix ambiguous."""
    client = _client(signed_in=False)
    _arm(client, "transient_load", 1)

    assert client.get("/search").status_code == 502
    after = client.get("/search")
    assert after.status_code == 303
    assert after.headers["location"] == "/login"


def test_armed_faults_are_consumed_in_the_documented_order_one_per_request() -> None:
    """Three faults armed at once surface one at a time: 502, then timeout, then the dialog.
    Each request consumes only the fault it surfaced, never a lower-priority one as well."""
    client = _client()
    for fault in (Fault.TRANSIENT_LOAD, Fault.SESSION_TIMEOUT, Fault.UNDECLARED_DIALOG):
        _arm(client, fault.value, 1)

    first = client.get("/search")
    assert first.status_code == 502
    assert _armed(client) == {"transient_load": 0, "session_timeout": 1, "undeclared_dialog": 1}

    second = client.get("/search")
    assert (second.status_code, second.headers["location"]) == (303, "/login")
    assert _armed(client) == {"transient_load": 0, "session_timeout": 0, "undeclared_dialog": 1}

    _sign_in(client)
    third = client.get("/search")
    assert "System Notice" in third.text
    assert _armed(client) == {"transient_load": 0, "session_timeout": 0, "undeclared_dialog": 0}


def test_an_unauthenticated_request_does_not_consume_the_undeclared_dialog() -> None:
    """A notice cannot interrupt a session that does not exist. If the login redirect ate it, an
    arm-then-sign-in harness would never see the dialog it armed."""
    client = _client(signed_in=False)
    _arm(client, "undeclared_dialog", 1)

    assert client.get("/search").status_code == 303

    assert _armed(client) == {"undeclared_dialog": 1}
    _sign_in(client)
    assert "System Notice" in client.get("/search").text


# ------------------------------------------------------- transient_load


def test_the_502_is_a_real_502_with_no_member_data_and_leaves_the_session_alone() -> None:
    """The recovery rule matches `http_status_in: [502,503,504]`, so the status must be genuine.
    A 502 must also not log the operator out: that would make every transient a session timeout."""
    client = _client()
    _arm(client, "transient_load", 1)

    blocked = client.get("/member/12345")

    assert blocked.status_code == 502
    assert "502" in blocked.text
    assert "Ada Lovelace" not in blocked.text
    assert "set-cookie" not in blocked.headers
    assert client.cookies.get(SESSION_COOKIE) == VALID_USER
    assert client.get("/member/12345").status_code == 200


def test_a_502_on_a_post_does_not_perform_the_action() -> None:
    """A transient failure before the handler runs must leave no partial effect: the search POST
    that was 502'd must not have redirected, and its retry must behave like a first attempt."""
    client = _client()
    _arm(client, "transient_load", 1)
    form = {BASE.field_member_no: "12345"}

    blocked = client.post("/search", data=form)
    retried = client.post("/search", data=form)

    assert blocked.status_code == 502
    assert "location" not in blocked.headers
    assert (retried.status_code, retried.headers["location"]) == (303, "/member/12345")


# ----------------------------------------------------- session_timeout


def test_a_session_timeout_clears_the_cookie_and_the_next_request_needs_a_fresh_login() -> None:
    client = _client()
    _arm(client, "session_timeout", 1)

    expired = client.get("/member/12345")

    assert (expired.status_code, expired.headers["location"]) == (303, "/login")
    assert 'mb_session=""' in expired.headers["set-cookie"]
    assert "Max-Age=0" in expired.headers["set-cookie"]
    assert not client.cookies.get(SESSION_COOKIE)
    # Consumed, but the operator is still signed out: expiry is a state change, not a blip.
    assert client.get("/member/12345").headers["location"] == "/login"


def test_re_authenticating_after_a_timeout_resumes_the_flow() -> None:
    """The declared remedy (`corebank.auth.sign_on`) depends on this: sign back on, retry."""
    client = _client()
    _arm(client, "session_timeout", 1)
    assert client.get("/search").status_code == 303

    _sign_in(client)

    assert client.get("/search").status_code == 200
    assert client.get("/member/12345").status_code == 200


def test_repeated_timeouts_each_require_their_own_re_authentication() -> None:
    client = _client()
    _arm(client, "session_timeout", 2)

    outcomes = []
    for _ in range(3):
        _sign_in(client)
        outcomes.append(client.get("/search").status_code)

    assert outcomes == [303, 303, 200]


def test_a_session_timeout_on_a_post_discards_the_submission() -> None:
    client = _client()
    _arm(client, "session_timeout", 1)

    response = client.post("/search", data={BASE.field_member_no: "12345"})

    assert (response.status_code, response.headers["location"]) == (303, "/login")


# --------------------------------------------------- undeclared_dialog


class _Attrs(HTMLParser):
    """Collects attributes by tag; HTMLParser decodes entities, as a browser does before JS runs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.seen: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.seen.append((tag, dict(attrs)))


def _continue_handler(html: str) -> str:
    """The JavaScript the browser would actually execute when 'Continue' is clicked."""
    parser = _Attrs()
    parser.feed(html)
    handlers = [
        attrs["onclick"]
        for tag, attrs in parser.seen
        if tag == "input" and attrs.get("value") == "Continue" and attrs.get("onclick")
    ]
    assert len(handlers) == 1, "the dialog must offer exactly one Continue control"
    handler = handlers[0]
    assert handler is not None
    return handler


def test_the_dialog_blocks_the_page_but_keeps_the_session_and_offers_continue() -> None:
    client = _client()
    _arm(client, "undeclared_dialog", 1)

    dialog = client.get("/member/12345")

    assert dialog.status_code == 200, "an unknown screen is not an HTTP error"
    assert "System Notice" in dialog.text
    assert "Ada Lovelace" not in dialog.text
    assert "set-cookie" not in dialog.headers
    assert _continue_handler(dialog.text) == "location='/member/12345';return false;"


def test_continuing_from_the_dialog_reaches_the_page_once_the_fault_is_spent() -> None:
    """Follow the Continue target exactly as the browser would: the dialog is a one-shot."""
    client = _client()
    _arm(client, "undeclared_dialog", 1)
    handler = _continue_handler(client.get("/member/12345").text)

    target = re.fullmatch(r"location='([^']*)';return false;", handler)
    assert target is not None
    landed = client.get(target.group(1))

    assert "Ada Lovelace" in landed.text


def test_an_indefinite_dialog_traps_continue_in_a_loop_of_dialogs() -> None:
    """With -1 the page is unreachable however often Continue is followed -- which is exactly
    why the replay must fail closed rather than click through."""
    client = _client()
    _arm(client, "undeclared_dialog", -1)

    for _ in range(5):
        landed = client.get("/member/12345")
        assert "System Notice" in landed.text
        assert "Ada Lovelace" not in landed.text


def test_a_dialog_swallows_a_post_so_the_action_never_happens() -> None:
    client = _client()
    _arm(client, "undeclared_dialog", 1)

    swallowed = client.post("/search", data={BASE.field_member_no: "12345"})

    assert swallowed.status_code == 200
    assert "System Notice" in swallowed.text
    assert "location" not in swallowed.headers
    assert client.post("/search", data={BASE.field_member_no: "12345"}).status_code == 303


def test_the_dialog_is_served_in_the_tenants_own_stylesheet() -> None:
    """The dialog must look like part of *this* tenant's product or it is not a fair 'harmless
    looking' interstitial. It links the variant's stylesheet and never the other tenant's."""
    client = _client("variant_b")
    _arm(client, "undeclared_dialog", 1)

    body = client.get("/search").text

    assert f"/static/{VARIANT_B.key}.css" in body
    assert f"/static/{BASE.key}.css" not in body
    assert VARIANT_B.css_button in body


HOSTILE_PATHS = [
    pytest.param("x'+alert(document.domain)+'", id="single-quote-breakout"),
    pytest.param("a\\", id="trailing-backslash-escapes-the-closing-quote"),
]


@pytest.mark.parametrize("member_id", ["12345", "unknown-member"])
def test_the_continue_handler_is_a_single_inert_string_literal_for_ordinary_paths(
    member_id: str,
) -> None:
    """Control for the xfail below: the checker accepts every well-formed path, so the xfail
    cannot be passing-by-accident because of a parser or regex mistake."""
    client = _client()
    _arm(client, "undeclared_dialog", 1)

    handler = _continue_handler(client.get(f"/member/{member_id}").text)

    assert re.fullmatch(r"location='[^'\\\r\n]*';return false;", handler)


@pytest.mark.parametrize("member_id", HOSTILE_PATHS)
def test_a_hostile_path_cannot_break_out_of_the_dialogs_continue_handler(member_id: str) -> None:
    client = _client()
    _arm(client, "undeclared_dialog", 1)

    response = client.get(f"/member/{quote(member_id, safe='')}")
    handler = _continue_handler(response.text)

    # Whatever the path held, what the browser executes must stay one string-literal assignment.
    assert re.fullmatch(r"location='[^'\\\r\n]*';return false;", handler), handler


# -------------------------------------------------- validation_error


def test_validation_error_fires_on_the_next_submission_whatever_it_contains() -> None:
    """Even an otherwise-invalid (empty) submission gets the armed rejection, not the ordinary
    required-field message: the fault is keyed to the submission, not to its content, so a
    capability's 'validation_rejected' outcome is reproducible from any input."""
    client = _client()
    _arm(client, "validation_error", 1)

    rejected = client.post("/member/12345/new-subaccount", data={})
    ordinary = client.post("/member/12345/new-subaccount", data={})

    assert "below the minimum" in rejected.text
    assert "below the minimum" not in ordinary.text
    assert "Account type is required." in ordinary.text


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        pytest.param("get", "/member/12345/new-subaccount", {}, id="GET-form-render"),
        pytest.param("post", "/search", {"data": {BASE.field_member_no: "12345"}}, id="search"),
        pytest.param("get", "/member/12345", {}, id="member-detail"),
        pytest.param(
            "post",
            "/member/99999/new-subaccount",
            {"data": {BASE.field_acct_type: "savings", BASE.field_deposit: "1"}},
            id="submission-for-an-unknown-member",
        ),
    ],
)
def test_validation_error_is_not_consumed_by_anything_but_a_real_submission(
    method: str, path: str, kwargs: dict[str, object]
) -> None:
    client = _client()
    _arm(client, "validation_error", 1)

    getattr(client, method)(path, **kwargs)

    assert _armed(client) == {"validation_error": 1}


def test_an_unauthenticated_submission_does_not_consume_validation_error() -> None:
    client = _client(signed_in=False)
    _arm(client, "validation_error", 1)

    response = client.post("/member/12345/new-subaccount", data={BASE.field_acct_type: "savings"})

    assert response.status_code == 303
    assert _armed(client) == {"validation_error": 1}


def test_a_dialog_in_front_of_the_form_keeps_the_validation_error_armed() -> None:
    """guard() runs before the handler, so a swallowed submission never reached the form logic and
    must not spend the validation fault."""
    client = _client()
    _arm(client, "undeclared_dialog", 1)
    _arm(client, "validation_error", 1)

    swallowed = client.post(
        "/member/12345/new-subaccount",
        data={BASE.field_acct_type: "savings", BASE.field_deposit: "1"},
    )

    assert "System Notice" in swallowed.text
    assert _armed(client) == {"undeclared_dialog": 0, "validation_error": 1}


def test_a_rejected_submission_rerenders_the_same_form_so_it_can_be_retried() -> None:
    client = _client()
    _arm(client, "validation_error", 1)
    form = {BASE.field_acct_type: "savings", BASE.field_deposit: "1.00"}

    rejected = client.post("/member/12345/new-subaccount", data=form)
    retried = client.post("/member/12345/new-subaccount", data=form)

    assert rejected.status_code == 200
    assert f'name="{BASE.field_acct_type}"' in rejected.text
    assert f'name="{BASE.field_deposit}"' in rejected.text
    assert BASE.confirm_heading not in rejected.text
    assert BASE.confirm_heading in retried.text


# -------------------------------------------- the control surface itself


UNKNOWN_FAULT_NAMES = [
    pytest.param("nope", id="unknown"),
    pytest.param("", id="empty"),
    pytest.param("   ", id="whitespace"),
    pytest.param("TRANSIENT_LOAD", id="wrong-case"),
    pytest.param(" transient_load", id="leading-space"),
    pytest.param("transient_load ", id="trailing-space"),
    pytest.param("transient-load", id="hyphenated"),
    pytest.param("transient_load​", id="zero-width-suffix"),
    pytest.param("trаnsient_load", id="cyrillic-a-homoglyph"),
    pytest.param("../../etc/passwd", id="path-traversal"),
    pytest.param("<script>alert(1)</script>", id="html"),
    pytest.param("'; DROP TABLE faults;--", id="sql"),
    pytest.param("{{7*7}}", id="template"),
    pytest.param("x" * 10_000, id="very-long"),
]


@pytest.mark.parametrize("name", UNKNOWN_FAULT_NAMES)
def test_an_unknown_fault_name_is_rejected_and_arms_nothing(name: str) -> None:
    """A typo must be loud. The dangerous outcome is a harness that thinks it armed a fault, runs
    the capability against a healthy app, and reports a clean pass for a test that never tested."""
    client = _client()
    _arm(client, "validation_error", 4)

    response = client.post("/_control/arm", json={"fault": name, "count": 3})

    assert response.status_code >= 400
    assert _armed(client) == {"validation_error": 4}, "a rejected arm must leave state untouched"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="missing-fault-key"),
        pytest.param({"count": 2}, id="count-only"),
        pytest.param({"fault": None}, id="null-fault"),
        pytest.param({"fault": 5}, id="numeric-fault"),
        pytest.param({"fault": ["transient_load"]}, id="list-fault"),
        pytest.param({"fault": "transient_load", "count": "abc"}, id="non-numeric-count"),
        pytest.param({"fault": "transient_load", "count": None}, id="null-count"),
        pytest.param({"fault": "transient_load", "count": [1]}, id="list-count"),
        pytest.param({"fault": "transient_load", "count": {"n": 1}}, id="object-count"),
    ],
)
def test_a_malformed_arm_payload_is_rejected_without_a_partial_arm(
    payload: dict[str, object],
) -> None:
    client = _client()
    _arm(client, "session_timeout", 2)

    response = client.post("/_control/arm", json=payload)

    assert response.status_code >= 400
    assert _armed(client) == {"session_timeout": 2}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"", id="empty-body"),
        pytest.param(b"not json", id="not-json"),
        pytest.param(b"[1, 2]", id="json-array"),
        pytest.param(b'"transient_load"', id="json-string"),
        pytest.param(b"null", id="json-null"),
        pytest.param(b'{"fault": "transient_load"', id="truncated-json"),
    ],
)
def test_a_non_object_arm_body_is_a_client_error(body: bytes) -> None:
    client = _client()

    response = client.post(
        "/_control/arm", content=body, headers={"content-type": "application/json"}
    )

    assert 400 <= response.status_code < 500
    assert _armed(client) == {}


def test_a_form_encoded_arm_is_refused_rather_than_half_understood() -> None:
    """The control surface speaks JSON. A form body must not be coerced into an arm."""
    client = _client()

    response = client.post("/_control/arm", data={"fault": "transient_load", "count": "1"})

    assert 400 <= response.status_code < 500
    assert _armed(client) == {}


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"fault": "nope"}, id="unknown-fault"),
        pytest.param({"fault": "transient_load", "count": "abc"}, id="bad-count"),
    ],
)
def test_a_rejected_arm_does_not_leak_source_or_a_traceback_to_the_caller(
    payload: dict[str, object],
) -> None:
    """Whatever the status, the body must not hand back internals (paths, frames, source)."""
    client = _client()

    response = client.post("/_control/arm", json=payload)

    assert response.status_code >= 400
    for needle in ("Traceback", "faults.py", "server.py", 'File "', "/Users/"):
        assert needle not in response.text


def test_the_control_endpoints_work_without_a_session() -> None:
    """They are out-of-band by design: a harness arms a fault *before* the run signs in."""
    client = _client(signed_in=False)

    assert client.post("/_control/arm", json={"fault": "transient_load"}).status_code == 200
    assert client.get("/_control/state").json()["armed"] == {"transient_load": 1}
    assert client.post("/_control/reset").status_code == 200


# ---------------------------------------------- FaultState, called directly


def test_arming_an_unknown_fault_raises_and_names_every_valid_fault() -> None:
    state = FaultState()

    with pytest.raises(ValueError, match="unknown fault") as raised:
        state.arm("nope")

    for fault in Fault:
        assert fault.value in str(raised.value)
    assert state.snapshot() == {}


def test_checking_an_unarmed_fault_does_not_create_state() -> None:
    """Probing must be free of side effects, or the snapshot stops meaning 'what was armed'."""
    state = FaultState()

    assert state.should_trigger(Fault.TRANSIENT_LOAD) is False

    assert state.snapshot() == {}


def test_the_snapshot_is_a_copy_not_a_live_view() -> None:
    state = FaultState()
    state.arm("transient_load", 2)

    state.snapshot()["transient_load"] = 99
    state.snapshot().clear()

    assert state.snapshot() == {"transient_load": 2}


def test_arming_with_the_enum_member_and_with_its_string_is_the_same_fault() -> None:
    state = FaultState()

    state.arm(Fault.TRANSIENT_LOAD, 1)
    state.arm("transient_load", 3)

    assert state.snapshot() == {"transient_load": 3}


@pytest.mark.parametrize("fault", ALL_FAULTS)
def test_every_fault_kind_is_armable_by_its_public_name(fault: Fault) -> None:
    """The four names the runbook and the CLI send over the wire are the enum's values."""
    state = FaultState()

    state.arm(fault.value, 1)

    assert state.should_trigger(fault) is True
    assert state.should_trigger(fault) is False


def test_the_documented_fault_vocabulary_has_not_drifted() -> None:
    """docs/runbooks/run-a-replay.md and the CLI arm faults by these literal names. Renaming one
    is a breaking change to every recorded harness, so it should take a deliberate edit here."""
    assert {fault.value for fault in Fault} == {
        "transient_load",
        "session_timeout",
        "undeclared_dialog",
        "validation_error",
    }
