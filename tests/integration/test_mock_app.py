"""The mock back-office behaves as the fixture contract requires.

This app is a fixture, not a deliverable, but it has to hold up two properties the rest of the
system depends on, and neither is self-evident:

1. **Deterministic.** Same request, same bytes, every time. Nondeterminism here would make every
   downstream replay failure ambiguous -- "is this our bug or the fixture's?" is the question that
   eats debugging days.
2. **Business outcomes are indistinguishable from success at the HTTP layer.** "No records found"
   returns 200 with no error styling, exactly as the real thing does. If the fixture made them easy
   to tell apart, the replay engine's classification logic would be tested against a strawman.
"""

from __future__ import annotations

import re

import pytest
from apps.mock_bank.server import SESSION_COOKIE, VALID_PW, VALID_USER, create_app
from apps.mock_bank.variants import BASE, VARIANT_B, Variant
from fastapi.testclient import TestClient


def client_for(variant: str = "base") -> TestClient:
    return TestClient(create_app(variant), follow_redirects=False)


def sign_in(client: TestClient, variant: Variant = BASE) -> None:
    response = client.post("/login", data={"user": VALID_USER, "pw": VALID_PW})
    assert response.status_code == 303
    assert client.cookies.get(SESSION_COOKIE) == VALID_USER


# --------------------------------------------------------------------- shape


def test_shell_is_a_frameset() -> None:
    """The legacy-web case the brief names: you cannot see anything without traversing frames."""
    client = client_for()
    body = client.get("/").text
    assert "<frameset" in body
    assert 'name="nav"' in body
    assert 'name="content"' in body


def test_no_test_ids_anywhere() -> None:
    """Legacy enterprise apps essentially never have these. Ours must not either, or the whole
    targeting problem this project exists to solve would be trivially easy."""
    client = client_for()
    sign_in(client)
    pages = ["/search", "/member/12345", "/member/12345/new-subaccount", "/nav"]
    for path in pages:
        body = client.get(path).text
        assert "data-testid" not in body, path
        assert "data-test" not in body, path
        assert not re.search(r'\bid=["\']', body), f"{path} leaked an id attribute"


def test_subaccount_controls_have_no_accessible_name() -> None:
    """The form controls carry no label, title, placeholder or id -- only an adjacent label cell.

    This is what forces `structural_anchor` to earn its place: there is genuinely no semantic name
    to match on, exactly as in the legacy apps this stands in for.
    """
    client = client_for()
    sign_in(client)
    body = client.get("/member/12345/new-subaccount").text
    select = re.search(r"<select[^>]*>", body)
    assert select is not None
    markup = select.group(0)
    for naming_attr in ("title=", "aria-label=", "id=", "placeholder="):
        assert naming_attr not in markup, f"select must have no accessible name: {naming_attr}"
    # ...but the label text is present in the row, which is the only way in.
    assert BASE.label_acct_type in body


# ------------------------------------------------------------- happy path


def test_full_flow_reaches_the_balance() -> None:
    client = client_for()
    sign_in(client)
    response = client.post("/search", data={BASE.field_member_no: "12345"})
    assert response.status_code == 303
    assert response.headers["location"] == "/member/12345"

    detail = client.get("/member/12345").text
    assert "Ada Lovelace" in detail
    assert BASE.label_savings in detail
    assert "$4,210.55" in detail


def test_subaccount_flow_reaches_confirmation() -> None:
    client = client_for()
    sign_in(client)
    response = client.post(
        "/member/12345/new-subaccount",
        data={BASE.field_acct_type: "savings", BASE.field_deposit: "100.00"},
    )
    assert response.status_code == 200
    assert BASE.confirm_heading in response.text
    assert "SA-12345-SAV" in response.text  # derived, not random -- a replay can assert on it


# ------------------------------------------------------- business outcomes
# Each of these is a legitimate answer the caller needs, NOT a failure. They all return 200.


@pytest.mark.parametrize(
    ("member_id", "marker"),
    [
        ("99999", "No records found"),
        ("55555", "not authorized"),
    ],
)
def test_business_outcomes_return_200_not_an_error(member_id: str, marker: str) -> None:
    client = client_for()
    sign_in(client)
    response = client.post("/search", data={BASE.field_member_no: member_id})
    assert response.status_code == 200, "a business outcome is not an HTTP error"
    assert marker in response.text


def test_closed_savings_account_is_visible_not_hidden() -> None:
    """24680's savings account exists but is Closed -- the capability must be able to see the
    distinction rather than reading a balance and assuming it is usable."""
    client = client_for()
    sign_in(client)
    body = client.get("/member/24680").text
    assert "Closed" in body


def test_member_with_no_savings_account() -> None:
    client = client_for()
    sign_in(client)
    body = client.get("/member/13579").text
    assert "No savings account on file" in body
    assert BASE.label_savings not in body


# ------------------------------------------------------------------ faults


def test_transient_load_returns_502_then_recovers() -> None:
    """Bounded and deterministic: exactly one failure, then success. This is what a declared
    recovery rule retries against."""
    client = client_for()
    sign_in(client)
    client.post("/_control/arm", json={"fault": "transient_load", "count": 1})

    assert client.get("/search").status_code == 502
    assert client.get("/search").status_code == 200


def test_transient_load_can_be_armed_indefinitely() -> None:
    """Drives RECOVERY_EXHAUSTED: a 'transient' condition that never clears must eventually stop
    being retried rather than hammering a struggling core banking system."""
    client = client_for()
    sign_in(client)
    client.post("/_control/arm", json={"fault": "transient_load", "count": -1})
    assert [client.get("/search").status_code for _ in range(4)] == [502] * 4


def test_session_timeout_drops_the_session() -> None:
    client = client_for()
    sign_in(client)
    client.post("/_control/arm", json={"fault": "session_timeout", "count": 1})

    response = client.get("/member/12345")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert not client.cookies.get(SESSION_COOKIE)


def test_undeclared_dialog_interrupts_with_an_unknown_screen() -> None:
    """The screen no capability declares. A replay landing here must fail closed with
    UNEXPECTED_STATE rather than clicking Continue -- which is why it is styled as a routine
    notice. The dangerous interstitial is the one that looks harmless."""
    client = client_for()
    sign_in(client)
    client.post("/_control/arm", json={"fault": "undeclared_dialog", "count": 1})

    body = client.get("/member/12345").text
    assert "System Notice" in body
    assert "Ada Lovelace" not in body, "the dialog must actually block the underlying page"


def test_validation_error_rejects_a_submission() -> None:
    client = client_for()
    sign_in(client)
    client.post("/_control/arm", json={"fault": "validation_error", "count": 1})

    response = client.post(
        "/member/12345/new-subaccount",
        data={BASE.field_acct_type: "savings", BASE.field_deposit: "1.00"},
    )
    assert response.status_code == 200
    assert "below the minimum" in response.text


def test_faults_are_consumed_not_sticky() -> None:
    client = client_for()
    sign_in(client)
    client.post("/_control/arm", json={"fault": "transient_load", "count": 2})
    assert client.get("/_control/state").json()["armed"] == {"transient_load": 2}

    client.get("/search")
    client.get("/search")
    assert client.get("/search").status_code == 200
    assert client.get("/_control/state").json()["armed"] == {"transient_load": 0}


def test_control_surface_is_not_discoverable_from_the_ui() -> None:
    """The agent must not be able to find the fault controls and disarm them. Nothing links here."""
    client = client_for()
    sign_in(client)
    for path in ["/", "/nav", "/search", "/member/12345", "/member/12345/new-subaccount"]:
        assert "_control" not in client.get(path).text, path


# ----------------------------------------------------------- determinism


@pytest.mark.parametrize("path", ["/search", "/member/12345", "/member/12345/new-subaccount"])
def test_pages_are_byte_identical_across_requests(path: str) -> None:
    """No clocks, no counters, no randomness. A flaky fixture makes every downstream failure
    ambiguous."""
    client = client_for()
    sign_in(client)
    first = client.get(path).content
    second = client.get(path).content
    assert first == second


# -------------------------------------------------------------- variant B


def test_variant_b_case_a_markup_churn_keeps_the_vocabulary() -> None:
    """Case A: everything a CSS selector could cache changes; the accessible name does not.

    This is the falsifiable form of the claim "this system does not depend on CSS selectors". The
    member-number field keeps its label and changes its class and form field name, so a recorded
    artifact's cached hints are all invalid -- and the control is still identifiable by role + name.
    """
    client = client_for("variant_b")
    response = client.post("/login", data={"user": VALID_USER, "pw": VALID_PW})
    assert response.status_code == 303

    body = client.get("/search").text

    # The vocabulary survives -> semantic_exact still resolves.
    assert VARIANT_B.label_member_no == BASE.label_member_no
    assert BASE.label_member_no in body

    # Everything a selector could have cached is gone.
    assert f'name="{BASE.field_member_no}"' not in body
    assert f'name="{VARIANT_B.field_member_no}"' in body
    assert BASE.css_input not in body
    assert VARIANT_B.css_input in body


def test_variant_b_case_b_rebranding_changes_the_vocabulary() -> None:
    """Case B: the label itself is rebranded.

    This defeats exact matching, normalized matching, AND structural anchoring -- because the anchor
    text *is* the label cell, and it changed too. There is deliberately no automatic recovery:
    guessing that "Savings Bal." means "Savings Balance" is exactly the kind of inference that ends
    with an automation reading the wrong row. A TenantBinding overlay supplies the new label.
    """
    client = client_for("variant_b")
    client.post("/login", data={"user": VALID_USER, "pw": VALID_PW})
    body = client.get("/member/12345").text

    assert VARIANT_B.label_savings != BASE.label_savings
    assert VARIANT_B.label_savings in body
    assert BASE.label_savings not in body

    # The label cell is relabeled along with everything else, so an anchor keyed on the base
    # label text has nothing to hold on to. This is the fact that justifies tenant overlays.
    assert f">{BASE.label_savings}<" not in body


def test_variant_b_preserves_row_structure() -> None:
    """Structure is what is genuinely stable about a vendor product across tenants: same flow, same
    rows, same ordering. Only the vocabulary and the styling move."""
    client = client_for("variant_b")
    client.post("/login", data={"user": VALID_USER, "pw": VALID_PW})
    body = client.get("/search").text
    assert "<table" in body and "<tr" in body and "<td" in body


def test_variant_b_serves_the_same_flow() -> None:
    """Same product, different build. The flow itself is what is genuinely stable about a vendor
    product, and therefore what the targeting strategy should lean on."""
    client = client_for("variant_b")
    client.post("/login", data={"user": VALID_USER, "pw": VALID_PW})

    response = client.post("/search", data={VARIANT_B.field_member_no: "12345"})
    assert response.status_code == 303

    detail = client.get("/member/12345").text
    assert "Ada Lovelace" in detail
    assert "$4,210.55" in detail
    assert VARIANT_B.label_savings in detail


# ------------------------------------------------------------------- auth


def test_unauthenticated_access_redirects_to_login() -> None:
    client = client_for()
    response = client.get("/member/12345")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_bad_credentials_are_rejected() -> None:
    client = client_for()
    response = client.post("/login", data={"user": VALID_USER, "pw": "wrong"})
    assert response.status_code == 200
    assert "Invalid operator ID" in response.text
    assert not client.cookies.get(SESSION_COOKIE)
