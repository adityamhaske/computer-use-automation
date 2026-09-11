"""Seeded member data for the mock back-office.

Entirely fictional. Names are historical computing figures; account numbers and balances are made
up. Nothing here resembles real PII, and the system still treats it as if it did -- that is the
point of the redaction invariant.

The data set is chosen so that each business outcome the capability schema declares has a member
that produces it. That is what makes the fault matrix writable:

    12345  happy path
    67890  happy path, different values (proves replay is parameterized, not memorized)
    24680  savings account closed        -> BUSINESS_OUTCOME(account_closed)
    13579  no savings account at all     -> BUSINESS_OUTCOME(no_savings_account)
    55555  restricted; teller lacks rights -> BUSINESS_OUTCOME(permission_denied)
    99999  does not exist                -> BUSINESS_OUTCOME(member_not_found)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Account:
    number: str
    kind: str  # "Savings" | "Checking" | "Certificate"
    balance: str  # formatted, as a legacy screen would render it
    status: str  # "Active" | "Closed" | "Dormant"


@dataclass(frozen=True)
class Member:
    member_id: str
    name: str
    joined: str
    branch: str
    accounts: tuple[Account, ...] = field(default_factory=tuple)
    restricted: bool = False


MEMBERS: dict[str, Member] = {
    "12345": Member(
        member_id="12345",
        name="Ada Lovelace",
        joined="03/14/2009",
        branch="Downtown",
        accounts=(
            Account("0001234501", "Savings", "$4,210.55", "Active"),
            Account("0001234502", "Checking", "$812.30", "Active"),
        ),
    ),
    "67890": Member(
        member_id="67890",
        name="Grace Hopper",
        joined="11/02/1997",
        branch="Riverside",
        accounts=(
            Account("0006789001", "Savings", "$18,730.00", "Active"),
            Account("0006789002", "Certificate", "$25,000.00", "Active"),
        ),
    ),
    "24680": Member(
        member_id="24680",
        name="Alan Turing",
        joined="06/23/2012",
        branch="Downtown",
        accounts=(Account("0002468001", "Savings", "$0.00", "Closed"),),
    ),
    "13579": Member(
        member_id="13579",
        name="Katherine Johnson",
        joined="08/26/2015",
        branch="Northgate",
        accounts=(Account("0001357901", "Checking", "$1,455.18", "Active"),),
    ),
    "55555": Member(
        member_id="55555",
        name="Restricted Member",
        joined="01/01/2020",
        branch="Executive",
        accounts=(Account("0005555501", "Savings", "$999,999.99", "Active"),),
        restricted=True,
    ),
}


def find_member(member_id: str) -> Member | None:
    """Look up a member. Returns None for 'no such member' -- a business outcome, not an error."""
    return MEMBERS.get(member_id.strip())


def savings_account(member: Member) -> Account | None:
    """The member's savings account, if they have one. None is a legitimate answer."""
    for account in member.accounts:
        if account.kind == "Savings":
            return account
    return None
