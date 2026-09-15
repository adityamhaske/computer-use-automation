"""The two suites.

`replay_stability` answers "does the same artifact keep working, and does it ever act wrongly?"
`cross_tenant` answers "does the same artifact work at a *different institution* without being
re-recorded?" -- the claim the write-up leans on hardest, and the one most worth making falsifiable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cua.domain.capability import Capability
from cua.domain.result import RunStatus
from cua.domain.run_record import RunRecord
from cua.domain.serde import load_capability
from cua.domain.tenant_binding import TenantBinding
from cua.evals.harness import Harness, mock_app
from cua.evals.scorers import GroundTruth, truth_key

FIXTURE = Path("tests/fixtures/capabilities/savings_balance.yaml")

# Seeded truth from apps/mock_bank/data.py. `as_of` is the screen's own date and moves daily, so the
# capability's checkpoint asserts its shape and this pins only what must never vary.
CASES: list[GroundTruth] = [
    GroundTruth(
        inputs={"member_id": "12345"},
        expect_status=RunStatus.SUCCESS,
        outputs={"savings_balance": "4210.55", "account_status": "Active"},
    ),
    GroundTruth(
        inputs={"member_id": "67890"},
        expect_status=RunStatus.SUCCESS,
        outputs={"savings_balance": "18730.00", "account_status": "Active"},
    ),
    GroundTruth(
        inputs={"member_id": "24680"},
        expect_status=RunStatus.SUCCESS,
        outputs={"savings_balance": "0.00", "account_status": "Closed"},
    ),
    GroundTruth(
        inputs={"member_id": "99999"},
        expect_status=RunStatus.BUSINESS_OUTCOME,
        outcome_code="member_not_found",
    ),
]


@dataclass
class SuiteResult:
    """One suite's runs, with enough context for the report to explain them."""

    name: str
    headline: str
    capability: Capability
    """The capability as executed: bound to this run's port, and overlaid for a tenant suite."""

    tenant: str | None
    source_hash: str = ""
    """Hash of the capability *as published*, before binding or overlay.

    The evaluation is evidence about a reviewed document, so it has to name that document.
    Recording the executed capability's hash instead produced a number matching nothing on
    disk -- three different hashes for one ref -- which made the evidence impossible to tie
    back to the artifact, and made `cua catalog approve --from-eval` unable to confirm that
    what was measured is what is being approved.
    """

    records: list[RunRecord] = field(default_factory=list)
    truth: dict[str, GroundTruth] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _source() -> Capability:
    """The capability as published, unbound.

    Read from the catalog rather than from the test fixture: an evaluation is evidence offered to
    a reviewer about the artifact they will approve, and measuring a different copy of it makes
    the evidence a statement about something else. Falls back to the fixture so the suite still
    runs in a checkout with an empty catalog.
    """
    published = Path("evidence/capabilities/corebank.member.savings_balance@1.0.0.yaml")
    source = published if published.exists() else FIXTURE
    return load_capability(source.read_text(encoding="utf-8"))


def _reference(base_url: str) -> Capability:
    """The published capability, bound to this run's port."""
    binding = TenantBinding.model_validate(
        {
            "capability_ref": "corebank.member.savings_balance@1.0.0",
            "tenant": "eval",
            "vars": {"base_url": base_url},
        }
    )
    return binding.apply(_source())


def _truth() -> dict[str, GroundTruth]:
    return {truth_key(c.inputs): c for c in CASES}


def replay_stability(*, repeats: int = 5, headless: bool = True) -> SuiteResult:
    """Replay the same artifact many times, over four members, on its own tenant.

    The headline is not the success rate -- a capability that always refused would score zero here
    and be perfectly safe. The headline is that the wrong-action count is zero while the success
    rate is high: the system answers, and when it answers it is right.
    """
    with mock_app("base") as app:
        capability = _reference(app.url)
        records = Harness(base_url=app.url, label="stability", headless=headless).run(
            capability, [dict(c.inputs) for c in CASES], repeats=repeats
        )
    return SuiteResult(
        name="replay_stability",
        headline="Does the same artifact keep working, and does it ever act on the wrong control?",
        capability=capability,
        tenant=None,
        source_hash=_source().content_hash,
        records=records,
        truth=_truth(),
        notes=[
            f"{len(CASES)} member(s) x {repeats} repeat(s), no model in the loop.",
            "Business outcomes count as successes: a correct negative answer is "
            "the system working.",
        ],
    )


def cross_tenant(*, repeats: int = 2, headless: bool = True) -> SuiteResult:
    """The same artifact at a second institution, reached by an overlay rather than a re-recording.

    Variant B rebrands the labels *and* restyles the markup, so every cached CSS hint is dead and
    `semantic_exact` no longer matches. A run can therefore only succeed by descending to
    `structural_anchor`. That is what makes "this system does not depend on CSS selectors"
    falsifiable rather than merely asserted -- if it did depend on them, this suite would fail.

    The overlay is four lines naming the new labels. That is the multi-tenant economics the write-up
    claims, measured: a rebranded tenant costs an overlay, not a fork.
    """
    with mock_app("variant_b") as app:
        base = _reference(app.url)
        binding = TenantBinding.model_validate(
            {
                "capability_ref": base.ref,
                "tenant": "northgate-fcu",
                "vars": {"base_url": app.url},
                "overrides": {
                    # Each rebranded step needs both halves restated: the anchor that *finds* the
                    # value, and the precondition that asserts this is the right screen. An overlay
                    # that only retargeted resolved the control correctly and then failed its own
                    # precondition -- which is fail-closed working exactly as designed, against an
                    # assertion written in another institution's vocabulary.
                    "read_balance": {
                        "target": {"anchor": {"relation": "adjacent_to", "text": "Savings Bal."}},
                        "precondition": {
                            "assert": "node_exists",
                            "query": {
                                "role": "cell",
                                "name_contains": "Savings Bal.",
                                "scope": {"frame": "content"},
                            },
                        },
                    },
                    "read_status": {
                        "target": {"anchor": {"relation": "adjacent_to", "text": "Acct Status"}},
                        "precondition": {
                            "assert": "node_exists",
                            "query": {
                                "role": "cell",
                                "name": "Acct Status",
                                "scope": {"frame": "content"},
                            },
                        },
                    },
                },
                # The success condition is stated in the recorded institution's words too.
                "checkpoint": {
                    "assert": "all_of",
                    "of": [
                        {
                            "assert": "node_exists",
                            "query": {"role": "cell", "name_contains": "Savings Bal."},
                        },
                        {
                            "assert": "node_exists",
                            "query": {
                                "role": "cell",
                                "name_matches": r"^\$[0-9,]+\.[0-9]{2}$",
                            },
                        },
                    ],
                },
            }
        )
        effective = binding.apply(base)
        records = Harness(base_url=app.url, label="cross-tenant", headless=headless).run(
            effective, [dict(c.inputs) for c in CASES], repeats=repeats
        )
    return SuiteResult(
        name="cross_tenant",
        headline="Does the same artifact serve a second institution without being re-recorded?",
        capability=effective,
        tenant="northgate-fcu",
        source_hash=_source().content_hash,
        records=records,
        truth=_truth(),
        notes=[
            "Variant B rebrands labels and restyles markup: every cached CSS hint is invalid.",
            "A tenant overlay names the new labels -- target, precondition and checkpoint. "
            "The base artifact is not forked.",
            'Every `hints.css` in the artifact (`input[name="memno"]`, `input.btn1`) names a '
            "selector that does not exist on this tenant. The runs succeed anyway, which is the "
            "no-CSS claim measured rather than asserted: if targeting depended on the hints, this "
            "suite would be at 0%.",
            "Case A (markup churn) is carried by `semantic_exact` -- the labels survived. Case B "
            "(rebranding) is carried by `structural_anchor` plus the overlay -- the labels did "
            "not. Both rungs are load-bearing here, which is why the ladder has both.",
        ],
    )


SUITES = {"replay_stability": replay_stability, "cross_tenant": cross_tenant}
