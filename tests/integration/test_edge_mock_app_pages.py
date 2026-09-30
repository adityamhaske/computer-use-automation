"""What the mock back-office shows: seeded outcomes, hostile identifiers, and the two tenants.

Three families of claim, each of which the rest of the suite leans on without asserting:

* every seeded member produces exactly the business outcome the docs promise, in both tenants, and
  the *other* outcomes never bleed into it (a denied page must not leak the record it denies);
* identifiers that look like attacks are just "no such member" -- echoed as text, never as markup,
  never interpreted as a template, path or query;
* Variant B is the same product in different clothes: the same page skeleton and the same data,
  with only vocabulary, class names and field names moved. That is what makes the cross-tenant
  reuse claim falsifiable rather than a property of a convenient fixture.

Offline: `fastapi.testclient` only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

import pytest
from apps.mock_bank.data import MEMBERS, find_member, savings_account
from apps.mock_bank.server import AS_OF, SESSION_COOKIE, VALID_PW, VALID_USER, create_app
from apps.mock_bank.variants import BASE, VARIANT_B, VARIANTS, Variant, get_variant
from fastapi.testclient import TestClient

TENANTS = [BASE, VARIANT_B]
TENANT_IDS = [v.key for v in TENANTS]


def _client(variant: str = "base", *, signed_in: bool = True) -> TestClient:
    client = TestClient(create_app(variant), follow_redirects=False)
    if signed_in:
        response = client.post("/login", data={"user": VALID_USER, "pw": VALID_PW})
        assert response.status_code == 303
    return client


# ----------------------------------------------------------- HTML helpers


@dataclass
class _Cell:
    css: str | None
    text: str = ""
    controls: list[tuple[str, dict[str, str | None]]] = field(default_factory=list)


class _Doc(HTMLParser):
    """A small structural reader: tables -> rows -> cells, plus every tag seen.

    Entities are decoded (`convert_charrefs`), so what this reports is what a browser would put in
    the DOM. That is the point: an escaped `&lt;b&gt;` is text, a raw `<b>` is an element.
    """

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[_Cell]]] = []
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self._rows: list[list[list[_Cell]]] = []
        self._row: list[list[_Cell] | None] = []
        self._cell: list[_Cell | None] = []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self.tags.append((tag, attributes))
        if tag == "table":
            self._rows.append([])
            self._row.append(None)
            self._cell.append(None)
        elif tag == "tr" and self._rows:
            self._row[-1] = []
        elif tag in ("td", "th") and self._rows:
            self._cell[-1] = _Cell(css=attributes.get("class"))
        elif tag in ("input", "select", "textarea", "button") and self._cell:
            cell = self._cell[-1]
            if cell is not None:
                cell.controls.append((tag, attributes))

    def handle_endtag(self, tag: str) -> None:
        if not self._rows:
            return
        if tag in ("td", "th"):
            cell, row = self._cell[-1], self._row[-1]
            if cell is not None and row is not None:
                row.append(cell)
            self._cell[-1] = None
        elif tag == "tr":
            row = self._row[-1]
            if row is not None:
                self._rows[-1].append(row)
            self._row[-1] = None
        elif tag == "table":
            self.tables.append(self._rows.pop())
            self._row.pop()
            self._cell.pop()

    def handle_data(self, data: str) -> None:
        if self._cell and self._cell[-1] is not None:
            cell = self._cell[-1]
            assert cell is not None
            cell.text += data

    @property
    def rows(self) -> list[list[_Cell]]:
        return [row for table in self.tables for row in table]

    def controls(self, *tags: str) -> list[dict[str, str | None]]:
        return [attrs for tag, attrs in self.tags if tag in tags]


def _norm(text: str) -> str:
    return " ".join(text.split())


def _doc(html: str) -> _Doc:
    return _Doc(html)


def _value_beside(doc: _Doc, label: str) -> list[str]:
    """Text of the cell next to every two-cell row whose label cell reads exactly `label`.

    Exactly two cells, because the accounts table also has a 'Status' *column header* and that is
    not the summary row a capability extracts from.
    """
    return [_norm(r[1].text) for r in doc.rows if len(r) == 2 and _norm(r[0].text) == label]


def _accounts(doc: _Doc) -> list[tuple[str, str, str, str]]:
    """The Accounts Overview table body: the four-cell rows that are not the header row."""
    rows = [r for r in doc.rows if len(r) == 4 and r[0].css is None]
    return [tuple(_norm(c.text) for c in r) for r in rows]  # type: ignore[misc]


def _not_found_text(doc: _Doc) -> str | None:
    for row in doc.rows:
        for cell in row:
            if _norm(cell.text).startswith("No records found"):
                return cell.text.strip()
    return None


# ------------------------------------------------------------ seeded data


@dataclass(frozen=True)
class Seed:
    name: str
    joined: str
    branch: str
    accounts: tuple[tuple[str, str, str, str], ...]
    savings: tuple[str, str] | None  # (balance, status) of the savings row, if any


SEEDED: dict[str, Seed] = {
    "12345": Seed(
        "Ada Lovelace",
        "03/14/2009",
        "Downtown",
        (
            ("0001234501", "Savings", "$4,210.55", "Active"),
            ("0001234502", "Checking", "$812.30", "Active"),
        ),
        ("$4,210.55", "Active"),
    ),
    "67890": Seed(
        "Grace Hopper",
        "11/02/1997",
        "Riverside",
        (
            ("0006789001", "Savings", "$18,730.00", "Active"),
            ("0006789002", "Certificate", "$25,000.00", "Active"),
        ),
        ("$18,730.00", "Active"),
    ),
    "24680": Seed(
        "Alan Turing",
        "06/23/2012",
        "Downtown",
        (("0002468001", "Savings", "$0.00", "Closed"),),
        ("$0.00", "Closed"),
    ),
    "13579": Seed(
        "Katherine Johnson",
        "08/26/2015",
        "Northgate",
        (("0001357901", "Checking", "$1,455.18", "Active"),),
        None,
    ),
}
RESTRICTED_ID = "55555"
ABSENT_ID = "99999"


def test_the_seeded_members_are_exactly_the_five_the_docs_promise() -> None:
    """README and the search screen advertise these ids; a sixth or a missing one changes what
    the fault matrix can be written against."""
    assert set(MEMBERS) == {*SEEDED, RESTRICTED_ID}
    assert ABSENT_ID not in MEMBERS
    assert [m.restricted for m in MEMBERS.values()].count(True) == 1
    assert MEMBERS[RESTRICTED_ID].restricted is True


def test_every_seeded_account_status_is_one_the_data_model_declares() -> None:
    """data.py declares Active | Closed | Dormant. (No seeded account is Dormant today; this
    guards the set so a typo like 'Actve' cannot become a fourth status the templates mis-style.)"""
    statuses = {a.status for m in MEMBERS.values() for a in m.accounts}
    assert statuses <= {"Active", "Closed", "Dormant"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12345", "12345"),
        ("  12345  ", "12345"),
        ("\t12345\n", "12345"),
        ("12345 ", "12345"),
        ("", None),
        ("   ", None),
        ("12345​", None),
        ("１２３４５", None),
        ("012345", None),
        ("12345; DROP TABLE members", None),
    ],
)
def test_find_member_canonicalises_whitespace_and_nothing_else(
    raw: str, expected: str | None
) -> None:
    member = find_member(raw)
    assert (member.member_id if member else None) == expected


def test_savings_account_is_none_for_a_member_without_one_and_not_a_checking_fallback() -> None:
    """None is a legitimate answer (BUSINESS_OUTCOME no_savings_account). Returning the checking
    account instead would be a silent wrong answer: a balance for the wrong product."""
    no_savings = find_member("13579")
    assert no_savings is not None
    assert savings_account(no_savings) is None
    with_savings = find_member("24680")
    assert with_savings is not None
    account = savings_account(with_savings)
    assert account is not None
    assert account.kind == "Savings"


# -------------------------------------------------- outcomes, both tenants


@pytest.mark.parametrize("member_id", sorted(SEEDED))
@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_a_seeded_member_renders_the_same_record_in_both_tenants(
    variant: Variant, member_id: str
) -> None:
    seed = SEEDED[member_id]
    doc = _doc(_client(variant.key).get(f"/member/{member_id}").text)

    assert _value_beside(doc, variant.label_member_no) == [member_id]
    assert _value_beside(doc, "Name") == [seed.name]
    assert _value_beside(doc, "Member Since") == [seed.joined]
    assert _value_beside(doc, "Branch") == [seed.branch]
    assert _accounts(doc) == list(seed.accounts)


@pytest.mark.parametrize("member_id", sorted(SEEDED))
@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_the_savings_summary_row_is_present_only_when_a_savings_account_exists(
    variant: Variant, member_id: str
) -> None:
    """What a capability extracts from. Absent for 13579 -- and its absence must read as the
    'No savings account' sentence, not as an empty value that looks like a $0 balance."""
    seed = SEEDED[member_id]
    body = _client(variant.key).get(f"/member/{member_id}").text
    doc = _doc(body)

    if seed.savings is None:
        assert _value_beside(doc, variant.label_savings) == []
        assert _value_beside(doc, "As Of") == []
        assert "No savings account on file for this member." in body
    else:
        balance, status = seed.savings
        assert _value_beside(doc, variant.label_savings) == [balance]
        assert _value_beside(doc, variant.label_status) == [status]
        assert _value_beside(doc, "As Of") == [AS_OF]
        assert _value_beside(doc, "Passbook Ref") == [""], "the deliberately blank field"
        assert "No savings account on file" not in body


@pytest.mark.parametrize("member_id", sorted(SEEDED))
@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_searching_a_visible_member_redirects_to_its_canonical_record(
    variant: Variant, member_id: str
) -> None:
    response = _client(variant.key).post("/search", data={variant.field_member_no: member_id})

    assert response.status_code == 303
    assert response.headers["location"] == f"/member/{member_id}"


@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_a_restricted_member_is_denied_without_leaking_any_of_the_record(variant: Variant) -> None:
    """permission_denied is an *answer*, so it has to say so -- but it must not also hand over the
    thing it denied. Checked on both routes that can reach the record."""
    client = _client(variant.key)
    secrets = ["Restricted Member", "Executive", "$999,999.99", "0005555501", "01/01/2020"]

    via_search = client.post("/search", data={variant.field_member_no: RESTRICTED_ID})
    via_url = client.get(f"/member/{RESTRICTED_ID}")

    for response in (via_search, via_url):
        assert response.status_code == 200, "a business outcome is not an HTTP error"
        assert "You are not authorized to view this member record." in response.text
        for secret in secrets:
            assert secret not in response.text


@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_the_three_answers_are_distinguishable_and_never_mistaken_for_each_other(
    variant: Variant,
) -> None:
    """member_not_found, permission_denied and a real record are three different outcomes. If the
    denied page said 'no records' the caller could not tell 'hidden' from 'absent'."""
    client = _client(variant.key)
    absent = client.post("/search", data={variant.field_member_no: ABSENT_ID}).text
    denied = client.post("/search", data={variant.field_member_no: RESTRICTED_ID}).text
    found = client.get("/member/12345").text

    assert "No records found" in absent and "not authorized" not in absent
    assert "not authorized" in denied and "No records found" not in denied
    assert "No records found" not in found and "not authorized" not in found
    assert len({absent, denied, found}) == 3


@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_looking_up_an_absent_member_is_identical_by_url_and_by_form(variant: Variant) -> None:
    """The two doors to the same answer must show the same thing, or a capability's outcome would
    depend on how it happened to arrive."""
    client = _client(variant.key)

    by_form = client.post("/search", data={variant.field_member_no: ABSENT_ID}).text
    by_url = client.get(f"/member/{ABSENT_ID}").text

    assert by_form == by_url


def test_repeated_lookups_are_byte_identical_for_every_outcome() -> None:
    """Determinism across outcomes, not just across the happy path."""
    client = _client()
    ids = [*SEEDED, RESTRICTED_ID, ABSENT_ID]

    first = [client.get(f"/member/{i}").content for i in ids]
    second = [client.get(f"/member/{i}").content for i in ids]

    assert first == second


# ----------------------------------------------------- identifier edge cases

# Identifiers that are not a member. Each must be a plain "no such member": fail closed to the
# business outcome rather than guessing that the caller "meant" 12345.
NOT_A_MEMBER = [
    pytest.param("１２３４５", id="fullwidth-digits"),
    pytest.param("٠١٢٣٤", id="arabic-indic-digits"),
    pytest.param("12345​", id="zero-width-space-suffix"),
    pytest.param("​12345", id="zero-width-space-prefix"),
    pytest.param("‏12345", id="rtl-mark-prefix"),
    pytest.param("12345́", id="combining-acute-suffix"),
    pytest.param("12З45", id="cyrillic-ze-homoglyph"),
    pytest.param("12345\x00", id="nul-suffix"),
    pytest.param("012345", id="leading-zero"),
    pytest.param("+12345", id="plus-sign"),
    pytest.param("12345.0", id="decimal"),
    pytest.param("12,345", id="thousands-separator"),
    pytest.param("12 345", id="internal-space"),
    pytest.param("abc", id="letters"),
    pytest.param("-1", id="negative"),
    pytest.param("9" * 10_000, id="ten-thousand-digits"),
    pytest.param("x" * 10_000, id="ten-thousand-letters"),
    pytest.param("12345' OR '1'='1", id="sql-tautology"),
    pytest.param("'; DROP TABLE members;--", id="sql-drop"),
    pytest.param("12345 UNION SELECT * FROM members", id="sql-union"),
    pytest.param("{{7*7}}", id="jinja-template"),
    pytest.param("${7*7}", id="dollar-template"),
    pytest.param("{% raw %}", id="jinja-statement"),
    pytest.param("%s%s%s%n", id="printf-format"),
    pytest.param("{0.__class__}", id="python-format-string"),
    pytest.param("../../etc/passwd", id="path-traversal"),
    pytest.param("12345/../99999", id="path-traversal-adjacent"),
]

# Same, but only the ones that can travel as one URL path segment.
_NO_SLASH = [p for p in NOT_A_MEMBER if "/" not in str(p.values[0])]


def _assert_plain_not_found(response: Any) -> None:
    assert response.status_code == 200, "a business outcome is not an HTTP error"
    doc = _doc(response.text)
    assert _not_found_text(doc) is not None, "expected the 'No records found' answer"
    for seed in SEEDED.values():
        assert seed.name not in response.text
    assert "Restricted Member" not in response.text
    assert "root:" not in response.text  # nothing read off the filesystem


@pytest.mark.parametrize("member_id", NOT_A_MEMBER)
def test_an_unrecognisable_id_posted_to_search_is_a_plain_no_such_member(member_id: str) -> None:
    response = _client().post("/search", data={BASE.field_member_no: member_id})

    _assert_plain_not_found(response)


@pytest.mark.parametrize("member_id", _NO_SLASH)
def test_an_unrecognisable_id_in_the_url_is_a_plain_no_such_member(member_id: str) -> None:
    response = _client().get(f"/member/{quote(member_id, safe='')}")

    _assert_plain_not_found(response)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("  12345", id="leading-spaces"),
        pytest.param("12345  ", id="trailing-spaces"),
        pytest.param("\t\n12345\r\n", id="tabs-and-newlines"),
        pytest.param(" 12345 ", id="non-breaking-spaces-pasted-from-email"),
    ],
)
def test_whitespace_padding_is_ignored_consistently_by_form_and_url(raw: str) -> None:
    """Both doors canonicalise the same way, and the canonical id is what the record shows --
    the padded form is never echoed back or used to build the redirect."""
    client = _client()

    via_form = client.post("/search", data={BASE.field_member_no: raw})
    via_url = client.get(f"/member/{quote(raw, safe='')}")

    assert (via_form.status_code, via_form.headers["location"]) == (303, "/member/12345")
    assert via_url.status_code == 200
    assert _value_beside(_doc(via_url.text), BASE.label_member_no) == ["12345"]


@pytest.mark.parametrize("raw", ["", " ", "\t", " ", "\n\n"])
def test_a_blank_member_number_is_a_form_error_not_a_lookup(raw: str) -> None:
    """Blank is 'you did not ask', which is different from 'no such member'. Conflating them
    would report a member_not_found for a question nobody asked."""
    response = _client().post("/search", data={BASE.field_member_no: raw})

    assert response.status_code == 200
    assert "Member number is required." in response.text
    assert "No records found" not in response.text


def test_a_search_without_the_field_at_all_is_the_same_form_error() -> None:
    assert "Member number is required." in _client().post("/search", data={}).text


def test_each_tenant_ignores_the_other_tenants_member_field_name() -> None:
    """The field name is per-tenant; posting the wrong tenant's name is 'no input', not a lookup."""
    base_client = _client("base")
    b_client = _client("variant_b")

    assert "required" in base_client.post("/search", data={VARIANT_B.field_member_no: "12345"}).text
    assert "required" in b_client.post("/search", data={BASE.field_member_no: "12345"}).text


@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_a_duplicated_member_field_resolves_deterministically_to_one_outcome(
    variant: Variant,
) -> None:
    """HTTP parameter pollution: two values for one field. Which wins is an implementation detail;
    that the answer is stable, and is one of the two real answers, is the claim."""
    client = _client(variant.key)
    body = f"{variant.field_member_no}=99999&{variant.field_member_no}=12345"
    headers = {"content-type": "application/x-www-form-urlencoded"}

    results = [client.post("/search", content=body, headers=headers) for _ in range(3)]

    shapes = {(r.status_code, r.headers.get("location"), r.text) for r in results}
    assert len(shapes) == 1
    first = results[0]
    assert first.headers.get("location") == "/member/12345" or "No records found" in first.text


@pytest.mark.parametrize(
    "member_id",
    [
        pytest.param("<script>alert(1)</script>", id="script-tag"),
        pytest.param('"><img src=x onerror=alert(1)>', id="attribute-breakout"),
        pytest.param("</td></tr></table><h1>owned</h1>", id="table-breakout"),
        pytest.param("a&amp;b &lt;i&gt;", id="pre-escaped-entities"),
        pytest.param("{{ config }}", id="template-expression"),
        pytest.param("line1\nline2", id="embedded-newline"),
        pytest.param("</title><script>1</script>", id="title-breakout"),
    ],
)
def test_a_hostile_id_is_echoed_as_text_and_never_becomes_markup(member_id: str) -> None:
    """The not-found page reflects the input, which is how reflected XSS starts. Read back through
    an HTML parser, the page must say exactly what was typed and contain no element it injected."""
    response = _client().post("/search", data={BASE.field_member_no: member_id})

    doc = _doc(response.text)
    assert _not_found_text(doc) == f"No records found for member number {member_id.strip()}."
    tags = {tag for tag, _ in doc.tags}
    assert not {"script", "img", "h1", "i"} & tags, "the id injected an element"
    assert "onerror" not in {k for _, attrs in doc.tags for k in attrs}


def test_a_hostile_id_in_the_url_is_echoed_as_text_too() -> None:
    # No slash: an encoded `/` is decoded before routing and never reaches the handler.
    hostile = '<img src=x onerror="alert(1)">'

    response = _client().get(f"/member/{quote(hostile, safe='')}")

    doc = _doc(response.text)
    assert _not_found_text(doc) == f"No records found for member number {hostile}."
    assert "img" not in {tag for tag, _ in doc.tags}


@pytest.mark.parametrize(
    "path",
    [
        "/member/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "/member/..%2f12345",
        "/member/%2e%2e/12345",
        "/member/12345%2f..%2f99999",
        "/member//12345",
        "/member/",
        "/static/..%2fserver.py",
        "/static/%2e%2e/server.py",
        "/static/../server.py",
        "/static/%2e%2e%2f%2e%2e%2f.env",
    ],
)
def test_path_traversal_shapes_never_reach_a_record_or_a_file(path: str) -> None:
    response = _client().get(path)

    assert response.status_code in (404, 307)
    if response.status_code == 307:  # slash normalisation: must stay on this app's own record
        assert response.headers["location"].endswith("/member/12345")
    for needle in ("root:", "VALID_PW", "create_app", "OMNIROUTE", "API_KEY"):
        assert needle not in response.text


# -------------------------------------------------------- sub-account form


def _submit(client: TestClient, variant: Variant, acct: str | None, deposit: str | None) -> Any:
    data: dict[str, str] = {}
    if acct is not None:
        data[variant.field_acct_type] = acct
    if deposit is not None:
        data[variant.field_deposit] = deposit
    return client.post("/member/12345/new-subaccount", data=data)


def _error_line(html: str) -> str | None:
    """The red error row, if any: the cell the templates wrap in a `<font color>`."""
    parser = _Doc(html)
    return next(
        (_norm(cell.text) for row in parser.rows for cell in row if "required" in cell.text),
        None,
    )


@pytest.mark.parametrize(
    ("acct", "deposit", "message"),
    [
        (None, None, "Account type is required."),
        ("", "", "Account type is required."),
        ("   ", "   ", "Account type is required."),
        (" ", "100", "Account type is required."),
        ("savings", None, "Initial deposit is required."),
        ("savings", "", "Initial deposit is required."),
        ("savings", "  \t ", "Initial deposit is required."),
        (None, "100", "Account type is required."),
    ],
)
@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_a_missing_field_is_a_field_level_error_on_the_same_form(
    variant: Variant, acct: str | None, deposit: str | None, message: str
) -> None:
    """Account type is checked first, so a form missing both reports the type, deterministically.
    And the error page is the form again, not a dead end: the retry has somewhere to go."""
    response = _submit(_client(variant.key), variant, acct, deposit)

    assert response.status_code == 200
    assert _error_line(response.text) == message
    assert f'name="{variant.field_acct_type}"' in response.text
    assert f'name="{variant.field_deposit}"' in response.text
    assert variant.confirm_heading not in response.text


@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_posting_the_other_tenants_field_names_is_a_missing_field_error(variant: Variant) -> None:
    other = VARIANT_B if variant is BASE else BASE

    response = _client(variant.key).post(
        "/member/12345/new-subaccount",
        data={other.field_acct_type: "savings", other.field_deposit: "100"},
    )

    assert _error_line(response.text) == "Account type is required."


@pytest.mark.parametrize(
    "deposit",
    [
        "-100",
        "0",
        "abc",
        "1e309",
        "NaN",
        "Infinity",
        "０１２",
        "١٢٣",
        "999999999999999999999999",
        "1,000.00",
        "$5",
        "<b>1</b>",
        "x" * 10_000,
    ],
)
def test_the_deposit_amount_alone_never_causes_a_rejection(deposit: str) -> None:
    """Rejection is reachable only by arming `validation_error`. If an odd amount could reject, a
    replay's outcome would depend on its input as well as on the fault, and the fixture's claim
    that every exceptional state is explicitly armed would be false."""
    response = _submit(_client(), BASE, "savings", deposit)

    assert response.status_code == 200
    assert BASE.confirm_heading in response.text
    assert _error_line(response.text) is None


@pytest.mark.parametrize(
    ("acct_type", "reference"),
    [
        ("savings", "SA-12345-SAV"),
        ("checking", "SA-12345-CHE"),
        ("certificate", "SA-12345-CER"),
        ("Savings", "SA-12345-SAV"),
        ("  checking  ", "SA-12345-CHE"),
        ("a", "SA-12345-A"),
        ("ab", "SA-12345-AB"),
        ("ß", "SA-12345-SS"),
        ("日本語", "SA-12345-日本語"),
        ("mortgage", "SA-12345-MOR"),
    ],
)
def test_the_confirmation_reference_is_derived_from_the_inputs_alone(
    acct_type: str, reference: str
) -> None:
    """Short, non-ASCII and unlisted types all get a reference; none of them is an error. The
    reference is a pure function of (member, type), which is what lets a replay assert on it."""
    response = _submit(_client(), BASE, acct_type, "1")

    doc = _doc(response.text)
    assert _value_beside(doc, "Reference") == [reference]


def test_a_hostile_account_type_is_echoed_as_text_not_markup() -> None:
    hostile = "<script>alert(1)</script>"

    response = _submit(_client(), BASE, hostile, "1")

    doc = _doc(response.text)
    assert _value_beside(doc, BASE.label_acct_type) == [hostile]
    assert _value_beside(doc, "Reference") == ["SA-12345-<SC"]
    assert "script" not in {tag for tag, _ in doc.tags}


def test_a_hostile_deposit_is_echoed_as_text_not_markup() -> None:
    hostile = '"><img src=x onerror=alert(1)>'

    response = _submit(_client(), BASE, "savings", hostile)

    doc = _doc(response.text)
    assert _value_beside(doc, BASE.label_initial_deposit) == [hostile]
    assert "img" not in {tag for tag, _ in doc.tags}


def test_the_confirmation_is_built_from_the_canonical_member_not_the_padded_url() -> None:
    """`/member/%2012345%20/...` finds the member; the reference and the return link must use the
    canonical id, or the padding would leak into the derived reference."""
    client = _client()

    response = client.post(
        f"/member/{quote(' 12345 ', safe='')}/new-subaccount",
        data={BASE.field_acct_type: "savings", BASE.field_deposit: "1"},
    )

    assert _value_beside(_doc(response.text), "Reference") == ["SA-12345-SAV"]
    assert 'href="/member/12345"' in response.text


def test_submitting_twice_is_idempotent_and_the_fixture_holds_no_state() -> None:
    """The confirmation says 'recorded', but the fixture is stateless on purpose: two identical
    submissions give the same bytes, and the member record is untouched afterwards. A counter or a
    stored row would make replay N differ from replay 1."""
    client = _client()
    before = client.get("/member/12345").content
    form = {BASE.field_acct_type: "savings", BASE.field_deposit: "250.00"}

    first = client.post("/member/12345/new-subaccount", data=form)
    second = client.post("/member/12345/new-subaccount", data=form)

    assert first.content == second.content
    assert client.get("/member/12345").content == before


@pytest.mark.parametrize("method", ["get", "post"])
def test_the_subaccount_routes_report_an_absent_member_as_no_such_member(method: str) -> None:
    response = getattr(_client(), method)(f"/member/{ABSENT_ID}/new-subaccount")

    assert response.status_code == 200
    assert _not_found_text(_doc(response.text)) is not None


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_restricted_member_is_denied_on_the_subaccount_routes_too(method: str) -> None:
    """README: '55555 restricted record -> permission_denied'. The record is denied on the detail
    and search routes, so the teller must not be able to open a sub-account for, or even see the
    name of, the member whose record they cannot view."""
    response = getattr(_client(), method)(
        f"/member/{RESTRICTED_ID}/new-subaccount",
        **(
            {"data": {BASE.field_acct_type: "savings", BASE.field_deposit: "1"}}
            if method == "post"
            else {}
        ),
    )

    assert "not authorized" in response.text
    assert "Restricted Member" not in response.text
    assert "Request Recorded" not in response.text


# ------------------------------------------------- the two tenants compared

# Pages reachable in both tenants. Each is fetched fresh so no test depends on another's state.
PAGES = [
    "login",
    "login_error",
    "search",
    "search_required_error",
    "not_found",
    "denied",
    "detail_12345",
    "detail_67890",
    "detail_24680",
    "detail_13579",
    "subaccount_form",
    "subaccount_required_error",
    "subaccount_validation_fault",
    "confirm",
    "reports_stub",
    "dialog",
]


def _fetch(page: str, variant: Variant) -> str:
    client = _client(variant.key, signed_in=not page.startswith("login"))
    form = {variant.field_acct_type: "savings", variant.field_deposit: "100.00"}
    if page == "login":
        return client.get("/login").text
    if page == "login_error":
        return client.post("/login", data={"user": "nobody", "pw": "wrong"}).text
    if page == "search":
        return client.get("/search").text
    if page == "search_required_error":
        return client.post("/search", data={}).text
    if page == "not_found":
        return client.post("/search", data={variant.field_member_no: ABSENT_ID}).text
    if page == "denied":
        return client.post("/search", data={variant.field_member_no: RESTRICTED_ID}).text
    if page.startswith("detail_"):
        return client.get(f"/member/{page.removeprefix('detail_')}").text
    if page == "subaccount_form":
        return client.get("/member/12345/new-subaccount").text
    if page == "subaccount_required_error":
        return client.post("/member/12345/new-subaccount", data={}).text
    if page == "subaccount_validation_fault":
        client.post("/_control/arm", json={"fault": "validation_error", "count": 1})
        return client.post("/member/12345/new-subaccount", data=form).text
    if page == "confirm":
        return client.post("/member/12345/new-subaccount", data=form).text
    if page == "reports_stub":
        return client.get("/reports").text
    if page == "dialog":
        client.post("/_control/arm", json={"fault": "undeclared_dialog", "count": 1})
        return client.get("/search").text
    raise AssertionError(f"unknown page {page!r}")


_VOCABULARY = (
    "institution",
    "app_title",
    "search_heading",
    "detail_heading",
    "confirm_heading",
    "label_member_no",
    "label_search_btn",
    "label_savings",
    "label_status",
    "label_acct_type",
    "label_initial_deposit",
    "label_open_btn",
    "label_confirm_btn",
)
_CSS = (
    "css_form_table",
    "css_label_cell",
    "css_input",
    "css_button",
    "css_data_table",
    "css_banner",
)
_FIELDS = ("field_member_no", "field_acct_type", "field_deposit")


class _Skeleton(HTMLParser):
    """The page as a tenant-neutral event stream.

    Vocabulary, class names and form field names are replaced by the *name of the Variant field*
    they came from, so two tenants that differ only in those three things produce the same stream.
    Anything else that differs -- an extra row, a moved control, different data -- survives into
    the stream and fails the comparison. That is the claim: same structure, different skin.
    """

    def __init__(self, variant: Variant) -> None:
        super().__init__(convert_charrefs=True)
        self.v = variant
        self.events: list[tuple[Any, ...]] = []
        self._text = ""
        self._by_vocab = sorted(_VOCABULARY, key=lambda f: -len(getattr(variant, f)))
        self._css = {getattr(variant, f): f"<{f}>" for f in _CSS}
        self._fields = {getattr(variant, f): f"<{f}>" for f in _FIELDS}

    def _vocab(self, text: str) -> str:
        out = _norm(text)
        for name in self._by_vocab:
            out = out.replace(getattr(self.v, name), f"<{name}>")
        return out

    def _flush(self) -> None:
        if self._text.strip():
            self.events.append(("text", self._vocab(self._text)))
        self._text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._flush()
        canon: list[tuple[str, str | None]] = []
        for key, value in attrs:
            if value is None:
                canon.append((key, None))
            elif key == "class":
                canon.append((key, " ".join(self._css.get(t, t) for t in value.split())))
            elif key == "name":
                canon.append((key, self._fields.get(value, value)))
            elif key in ("title", "value", "placeholder"):
                canon.append((key, self._vocab(value)))
            elif key in ("href", "src"):
                canon.append((key, value.replace(f"/static/{self.v.key}.css", "/static/<key>.css")))
            else:
                canon.append((key, value))
        self.events.append(("start", tag, tuple(canon)))

    def handle_endtag(self, tag: str) -> None:
        self._flush()
        self.events.append(("end", tag))

    def handle_data(self, data: str) -> None:
        self._text += data

    def close(self) -> None:
        super().close()
        self._flush()


def _skeleton(html: str, variant: Variant) -> list[tuple[Any, ...]]:
    parser = _Skeleton(variant)
    parser.feed(html)
    parser.close()
    return parser.events


@pytest.mark.parametrize("page", PAGES)
def test_variant_b_is_the_same_page_skeleton_as_base_with_only_its_skin_changed(
    page: str,
) -> None:
    """Row order, control order, data, flow: identical. Vocabulary, class names and field names
    are the only things allowed to move (variants.py: 'NOT here: page structure')."""
    base = _skeleton(_fetch(page, BASE), BASE)
    tenant_b = _skeleton(_fetch(page, VARIANT_B), VARIANT_B)

    assert base, "the skeleton must not be empty or the comparison proves nothing"
    assert base == tenant_b


@pytest.mark.parametrize("page", PAGES)
def test_the_two_tenants_do_not_serve_byte_identical_pages(page: str) -> None:
    """Guards the test above against comparing a tenant with itself: the skins really differ."""
    assert _fetch(page, BASE) != _fetch(page, VARIANT_B)


def _class_tokens(html: str) -> set[str]:
    return {token for _, attrs in _Doc(html).tags for token in (attrs.get("class") or "").split()}


def _field_names(html: str) -> set[str]:
    return {attrs["name"] for attrs in _Doc(html).controls("input", "select") if attrs.get("name")}


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_no_page_carries_the_other_tenants_classes_or_field_names(
    variant: Variant, page: str
) -> None:
    """Variant B must invalidate every cached CSS hint, so not one of base's class names or form
    field names may survive into it -- and vice versa. A single shared class would be a selector
    that still works, which is the thing the cross-tenant test claims cannot happen."""
    other = VARIANT_B if variant is BASE else BASE
    html = _fetch(page, variant)

    assert not _class_tokens(html) & {getattr(other, f) for f in _CSS}
    assert not _field_names(html) & {getattr(other, f) for f in _FIELDS}


def test_the_tenants_share_no_css_class_and_no_form_field_name() -> None:
    """The precondition of the test above, stated on the data: nothing to accidentally share."""
    for names in (_CSS, _FIELDS):
        base_values = {getattr(BASE, n) for n in names}
        b_values = {getattr(VARIANT_B, n) for n in names}
        assert len(base_values) == len(names), "a tenant reuses one name for two roles"
        assert len(b_values) == len(names)
        assert not base_values & b_values


def test_case_a_fields_keep_their_label_and_case_b_fields_do_not() -> None:
    """The two kinds of divergence variants.py names, pinned on the data."""
    assert VARIANT_B.label_member_no == BASE.label_member_no
    assert VARIANT_B.label_search_btn == BASE.label_search_btn
    assert VARIANT_B.label_savings != BASE.label_savings
    assert VARIANT_B.label_acct_type != BASE.label_acct_type


def test_unknown_variant_keys_are_refused_with_the_valid_choices_named() -> None:
    for bad in ("", "Base", "BASE", "variant_c", "base ", "../base"):
        with pytest.raises(ValueError, match="unknown variant") as raised:
            get_variant(bad)
        assert "base" in str(raised.value) and "variant_b" in str(raised.value)
    with pytest.raises(ValueError, match="unknown variant"):
        create_app("variant_c")
    assert set(VARIANTS) == {"base", "variant_b"}


@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_each_tenants_stylesheet_is_served_and_styles_every_class_the_markup_emits(
    variant: Variant,
) -> None:
    """An unstyled tenant would still pass every structural test and look nothing like a product;
    the classes are the contract between template and stylesheet."""
    client = _client(variant.key, signed_in=False)

    css = client.get(f"/static/{variant.key}.css")

    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    for name in _CSS:
        assert f".{getattr(variant, name)}" in css.text


@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_every_page_links_its_own_tenants_stylesheet_only(variant: Variant) -> None:
    other = VARIANT_B if variant is BASE else BASE
    for page in ("search", "detail_12345", "subaccount_form", "confirm", "denied"):
        html = _fetch(page, variant)
        assert f"/static/{variant.key}.css" in html
        assert f"/static/{other.key}.css" not in html


# ---------------------------------------------------- hostile-by-design


@pytest.mark.parametrize("page", [p for p in PAGES if not p.startswith("login")])
@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_no_member_flow_page_carries_an_id_or_a_data_attribute(variant: Variant, page: str) -> None:
    """Legacy apps ship no test ids. Checked on parsed attributes across every page and both
    tenants -- error re-renders included, since a sloppy template edit adds an id to exactly the
    page nobody looked at."""
    attribute_names = {key for _, attrs in _Doc(_fetch(page, variant)).tags for key in attrs}

    assert "id" not in attribute_names
    assert not {k for k in attribute_names if k.startswith("data-")}


def _subaccount_pages(variant: Variant) -> dict[str, str]:
    return {
        "form": _fetch("subaccount_form", variant),
        "required-error": _fetch("subaccount_required_error", variant),
        "validation-fault": _fetch("subaccount_validation_fault", variant),
    }


@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_subaccount_controls_stay_unnamed_on_the_form_and_on_every_error_rerender(
    variant: Variant,
) -> None:
    """The fixture's reason to exist is that these controls have no accessible name, so the only
    way in is the label cell beside them. That has to hold on the re-rendered error pages too --
    a retry after a rejection lands on those, and a 'helpful' title added there would make the
    retry resolve by name while the first attempt resolved structurally."""
    for label, html in _subaccount_pages(variant).items():
        doc = _Doc(html)
        fields = [
            c for c in doc.controls("input", "select", "textarea") if c.get("type") != "submit"
        ]
        assert len(fields) == 2, f"{label}: expected the type select and the deposit input"
        for control in fields:
            for naming in ("title", "aria-label", "aria-labelledby", "id", "placeholder", "alt"):
                assert naming not in control, f"{label}: control gained an accessible name"
        assert "label" not in {tag for tag, _ in doc.tags}, f"{label}: a <label> names the controls"
        assert "for" not in {k for _, attrs in doc.tags for k in attrs}


@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_each_unnamed_control_sits_in_the_row_whose_label_cell_names_it(variant: Variant) -> None:
    """Structural anchoring needs exactly this: label cell, then the control in the next cell."""
    doc = _Doc(_fetch("subaccount_form", variant))
    rows = {_norm(r[0].text): r for r in doc.rows if len(r) == 2}

    type_row = rows[variant.label_acct_type]
    deposit_row = rows[variant.label_initial_deposit]

    assert [(t, a.get("name")) for t, a in type_row[1].controls] == [
        ("select", variant.field_acct_type)
    ]
    assert [(t, a.get("name")) for t, a in deposit_row[1].controls] == [
        ("input", variant.field_deposit)
    ]


@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_the_search_field_is_the_one_control_that_does_carry_a_name(variant: Variant) -> None:
    """The contrast that lets one artifact exercise both ends of the ladder: the member-number
    input has a title equal to its label in both tenants, and sits beside that label."""
    doc = _Doc(_fetch("search", variant))

    row = next(r for r in doc.rows if len(r) == 2 and _norm(r[0].text) == variant.label_member_no)
    [(tag, attrs)] = row[1].controls
    assert (tag, attrs["type"], attrs["name"]) == ("input", "text", variant.field_member_no)
    assert attrs["title"] == variant.label_member_no


# --------------------------------------------------------- frameset & nav


class _Frames(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.frames: list[dict[str, str | None]] = []
        self.frameset: dict[str, str | None] = {}
        self.has_noframes = False
        self.scripts = ""
        self._in_script = False
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "frame":
            self.frames.append(dict(attrs))
        elif tag == "frameset":
            self.frameset = dict(attrs)
        elif tag == "noframes":
            self.has_noframes = True
        elif tag == "script":
            self._in_script = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self.scripts += data


@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_the_frameset_has_the_same_two_named_frames_in_every_tenant(variant: Variant) -> None:
    frames = _Frames(_client(variant.key).get("/").text)

    assert frames.frameset["cols"] == "220,*"
    assert [(f["name"], f["src"]) for f in frames.frames] == [
        ("nav", "/nav"),
        ("content", "/search"),
    ]
    assert frames.has_noframes, "a frame-less browser must be told why nothing renders"
    assert "window.top" in frames.scripts, "the frame-buster keeps the app out of foreign frames"


@pytest.mark.parametrize(
    "cookie",
    [
        pytest.param(None, id="no-cookie"),
        pytest.param("", id="empty-cookie"),
        pytest.param(" ", id="blank-cookie"),
    ],
)
def test_the_content_frame_opens_on_login_unless_a_session_cookie_exists(
    cookie: str | None,
) -> None:
    client = _client(signed_in=False)
    headers = {} if cookie is None else {"Cookie": f"{SESSION_COOKIE}={cookie}"}

    frames = _Frames(client.get("/", headers=headers).text)

    assert {f["name"]: f["src"] for f in frames.frames}["content"] == "/login"


def test_the_content_frame_opens_on_search_after_sign_in_and_on_login_after_sign_off() -> None:
    client = _client()
    signed_in = _Frames(client.get("/").text)
    client.get("/logout")
    signed_off = _Frames(client.get("/").text)

    assert {f["name"]: f["src"] for f in signed_in.frames}["content"] == "/search"
    assert {f["name"]: f["src"] for f in signed_off.frames}["content"] == "/login"


@pytest.mark.parametrize("variant", TENANTS, ids=TENANT_IDS)
def test_the_nav_frame_is_reachable_without_a_session_and_has_no_real_hrefs(
    variant: Variant,
) -> None:
    """Legacy nav: every link is `href="#"` plus an inline onclick, so a 'find the link' strategy
    has nothing to hold. The nav must load even signed out, because it is a sibling frame of the
    login screen."""
    response = _client(variant.key, signed_in=False).get("/nav")
    doc = _Doc(response.text)
    anchors = [attrs for tag, attrs in doc.tags if tag == "a"]

    assert response.status_code == 200
    assert len(anchors) == 3
    assert all(a["href"] == "#" for a in anchors)
    assert all("onclick" in a for a in anchors)
    assert "parent.content.location='/search'" in (anchors[0]["onclick"] or "")
    assert variant.search_heading in response.text
    # The two modules that are not built say so rather than navigating somewhere unknown.
    assert all("alert(" in (a["onclick"] or "") for a in anchors[1:])
