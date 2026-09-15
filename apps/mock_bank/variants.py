"""Two builds of the same vendor product, as two tenants would deploy it.

This is the stand-in for the multi-tenant reality in the brief: hundreds of institutions running the
same underlying vendor software, configured, branded and versioned differently.

`variant_b` deliberately contains TWO different kinds of divergence, because they need two
different answers and conflating them produces a design that guesses:

**A. Markup churn, same vocabulary.** The member-number field keeps its label ("Member Number") but
changes its CSS class, its form field name, and its styling. Every cached `hint.css` in a recorded
artifact is invalidated, and the control is still found -- by role and accessible name, then by the
row it sits in. This is what the resolution ladder handles *automatically*, and it is the claim
worth making falsifiable:

    This system does not depend on CSS selectors.

If it did, variant_b would fail here. It does not.

**B. Rebranding, different vocabulary.** The savings-balance row and the account-type control are
relabeled ("Savings Balance" -> "Savings Bal."). Note what this defeats: not just `semantic_exact`,
but `structural_anchor` too -- because the anchor text *is* the label, and the label changed. There
is no automatic recovery here, and that is the correct behaviour. Inferring that "Savings Bal."
means "Savings Balance" is a fuzzy guess, and a system that guesses which row holds a balance is a
system that will eventually read the wrong one.

So case B is what `TenantBinding` overlays exist for (ADR 0005): the replay fails closed with a high
drift score, a four-line overlay names the new label, and it succeeds. The demo is
*detect -> refuse -> cheap override*, not magic.

Discovered by spiking the accessibility tree against this app before building the resolver -- an
earlier draft of this file claimed structural anchoring survived rebranding, which the spike
disproved. See docs/adr/0001-uisnapshot-as-the-cross-surface-abstraction.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Variant:
    """A tenant's build of the product.

    `key` identifies the variant; everything else is what differs between deployments. Note what is
    NOT here: page structure. The table layout, row ordering, and form flow are identical across
    variants, because that is what is genuinely stable about a vendor product -- and therefore what
    the targeting strategy should lean on.
    """

    key: str
    institution: str

    # --- Branding: defeats naive text matching -------------------------------
    app_title: str
    search_heading: str
    detail_heading: str
    confirm_heading: str

    # --- Control labels: defeats exact and normalized name matching ----------
    label_member_no: str
    label_search_btn: str
    label_savings: str
    label_status: str
    label_acct_type: str
    label_initial_deposit: str
    label_open_btn: str
    label_confirm_btn: str

    # --- Form field names: invalidates cached CSS/attribute hints ------------
    field_member_no: str
    field_acct_type: str
    field_deposit: str

    # --- CSS classes: invalidates cached CSS hints ---------------------------
    css_form_table: str
    css_label_cell: str
    css_input: str
    css_button: str
    css_data_table: str
    css_banner: str


BASE = Variant(
    key="base",
    institution="First Valley Credit Union",
    app_title="MemberDesk",
    search_heading="Member Search",
    detail_heading="Member Detail",
    confirm_heading="Confirmation",
    label_member_no="Member Number",
    label_search_btn="Search",
    label_savings="Savings Balance",
    label_status="Status",
    label_acct_type="Account Type",
    label_initial_deposit="Initial Deposit",
    label_open_btn="Open Account",
    label_confirm_btn="Confirm",
    field_member_no="memno",
    field_acct_type="accttype",
    field_deposit="initdep",
    css_form_table="frmtbl",
    css_label_cell="lblcel",
    css_input="frmfld",
    css_button="btn1",
    css_data_table="dtatbl",
    css_banner="bnr",
)

VARIANT_B = Variant(
    key="variant_b",
    institution="Northgate Federal CU",
    app_title="MemberDesk Pro",
    search_heading="Find a Member",
    detail_heading="Member Record",
    confirm_heading="Request Submitted",
    label_member_no="Member Number",  # case A: label PRESERVED
    label_search_btn="Search",  # case A: label PRESERVED
    label_savings="Savings Bal.",  # case B: REBRANDED -> overlay
    label_status="Acct Status",
    label_acct_type="Type of Account",  # case B: REBRANDED -> overlay
    label_initial_deposit="Opening Deposit",
    label_open_btn="Submit Request",
    label_confirm_btn="Yes, Submit",
    field_member_no="member_num",
    field_acct_type="acct_kind",
    field_deposit="open_amt",
    css_form_table="ng-form-grid",
    css_label_cell="ng-cap",
    css_input="ng-inp",
    css_button="ng-act",
    css_data_table="ng-grid",
    css_banner="ng-head",
)

VARIANTS: dict[str, Variant] = {BASE.key: BASE, VARIANT_B.key: VARIANT_B}


def get_variant(key: str) -> Variant:
    if key not in VARIANTS:
        raise ValueError(f"unknown variant {key!r}; expected one of {sorted(VARIANTS)}")
    return VARIANTS[key]
