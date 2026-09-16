"""The catalog: how an agent finds a capability, and what it refuses to serve.

The interesting tests here are not "listing works". They are the two refusals -- an edited artifact
and a missing one -- because a catalog that serves capabilities it has not verified undoes the
reason they are content-addressed in the first place.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import typer

from cua.catalog.approvals import ApprovalStore
from cua.catalog.store import (
    CapabilityNotFoundError,
    CapabilityStore,
    CapabilityTamperedError,
)
from cua.catalog.toolspec import tool_definitions
from cua.cli.agent_demo import interpret
from cua.cli.main import catalog_approve
from cua.domain.approval import ApprovalState, CapabilityApproval
from cua.domain.evaluation import CapabilityEvaluation
from cua.domain.result import (
    BusinessOutcome,
    FailureCode,
    FailureDetail,
    InterventionRef,
    RunResult,
    RunStatus,
)
from cua.domain.serde import dump_capability, load_capability

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/capabilities/savings_balance.yaml"


@pytest.fixture
def store(tmp_path: Path) -> CapabilityStore:
    """A catalog holding one sealed capability.

    Sealed, because that is what a catalog holds in practice -- the compiler seals what it emits.
    The reference fixture is checked in unsealed on purpose (it is a hand-authored draft), so it is
    sealed here rather than changing what the schema tests read.
    """
    root = tmp_path / "capabilities"
    root.mkdir()
    sealed = dump_capability(load_capability(FIXTURE.read_text(encoding="utf-8")))
    (root / "savings.yaml").write_text(sealed, encoding="utf-8")
    return CapabilityStore(root=root)


# ----------------------------------------------------------------- finding one


def test_a_capability_is_listed_with_its_typed_signature(store: CapabilityStore) -> None:
    """What an agent needs to decide whether this is the right tool."""
    (entry,) = store.list()
    assert entry.capability.id == "corebank.member.savings_balance"
    assert "member_id: string" in entry.signature
    assert "savings_balance: money" in entry.signature


def test_a_bare_id_resolves_to_a_version(store: CapabilityStore) -> None:
    assert store.resolve("corebank.member.savings_balance").ref.endswith("@1.0.0")


def test_an_exact_ref_resolves(store: CapabilityStore) -> None:
    assert store.load("corebank.member.savings_balance@1.0.0").version == "1.0.0"


def test_a_missing_capability_says_what_is_available(store: CapabilityStore) -> None:
    """A bare "not found" makes the caller guess. Naming the catalog's contents does not."""
    with pytest.raises(CapabilityNotFoundError) as raised:
        store.load("corebank.wire.transfer")
    assert "corebank.member.savings_balance@1.0.0" in str(raised.value)


def test_an_empty_catalog_is_empty_not_an_error(tmp_path: Path) -> None:
    assert CapabilityStore(root=tmp_path / "nope").list() == []


def test_an_unparseable_file_is_skipped_not_fatal(store: CapabilityStore) -> None:
    """One malformed artifact must not make the whole catalog unusable."""
    (store.root / "broken.yaml").write_text("this: is: not: a: capability", encoding="utf-8")
    assert len(store.list()) == 1


# -------------------------------------------------------------- what it refuses


def test_an_artifact_edited_in_place_is_refused(store: CapabilityStore) -> None:
    """The content hash is load-bearing, not decorative.

    The threat is a YAML file edited on disk, not a capability re-sealed through the model --
    `dump_capability` recomputes the hash, which is correct for a legitimate change (that is what a
    version bump is). What must never load is a file whose declared hash no longer covers its
    content: if the catalog served it, "which version ran?" would have an answer that was not true.
    """
    path = store.root / "savings.yaml"
    before = path.read_text(encoding="utf-8")
    after = before.replace("savings balance", "CHECKING balance", 1)
    assert after != before, "the edit must actually change the file"
    path.write_text(after, encoding="utf-8")

    with pytest.raises(CapabilityTamperedError) as raised:
        store.load("corebank.member.savings_balance")
    assert "content hash" in str(raised.value)


def _as_draft(text: str) -> str:
    """The same artifact with its seal removed.

    These tests need *a* draft, not specifically the reviewed artifact. Reading the fixture raw
    made them depend on it being unsealed, so sealing it -- which is what a reviewed artifact
    should be -- broke two tests that had nothing to do with the change.
    """
    return re.sub(r"^content_hash:.*$", "", text, flags=re.M).rstrip() + "\n"


def test_an_unsealed_draft_loads_but_is_flagged(store: CapabilityStore) -> None:
    """A draft is a legitimate state, so it is served -- and visibly marked.

    Refusing drafts would make the catalog useless for exactly the artifact the compiler just
    produced. Serving one silently would let an unreviewed capability look identical to a reviewed
    one. `cua catalog list` prints the distinction.
    """
    (store.root / "savings.yaml").write_text(
        _as_draft(FIXTURE.read_text(encoding="utf-8")), encoding="utf-8"
    )
    entry = store.resolve("corebank.member.savings_balance")
    assert entry.capability.content_hash == ""
    assert not entry.capability.hash_is_valid()
    assert store.load("corebank.member.savings_balance") is not None


def test_a_resealed_artifact_loads(store: CapabilityStore) -> None:
    """The counterpart: a deliberate change, re-sealed, is a legitimate artifact."""
    capability = load_capability(FIXTURE.read_text(encoding="utf-8"))
    changed = capability.model_copy(update={"title": "Look up a member's savings balance (v2)"})
    (store.root / "savings.yaml").write_text(dump_capability(changed), encoding="utf-8")
    assert store.load("corebank.member.savings_balance").title.endswith("(v2)")


def test_the_tool_contract_comes_from_the_artifact(store: CapabilityStore) -> None:
    """There is no second source of truth to drift out of sync."""
    (schema,) = tool_definitions(store)
    assert schema["input_schema"]["required"] == ["member_id"]
    assert schema["input_schema"]["additionalProperties"] is False
    assert "member_not_found" in {o["code"] for o in schema["outcomes"]}


# ------------------------------------------------- what the calling agent does


def _result(status: RunStatus, **kwargs: object) -> RunResult:
    return RunResult(
        status=status,
        capability_id="corebank.member.savings_balance",
        capability_version="1.0.0",
        run_id="r",
        **kwargs,  # type: ignore[arg-type]
    )


def test_an_agent_uses_the_outputs_on_success() -> None:
    turn = interpret(_result(RunStatus.SUCCESS, outputs={"savings_balance": "$4,210.55"}))
    assert turn.disposition == "answered"
    assert turn.payload["savings_balance"] == "$4,210.55"
    assert turn.ok


def test_an_agent_treats_a_business_outcome_as_an_answer() -> None:
    """The distinction the brief calls the most common design mistake.

    An agent that collapses this into "error" retries a question the system already answered, and
    eventually escalates it to a human who has nothing to do.
    """
    turn = interpret(
        _result(RunStatus.BUSINESS_OUTCOME, outcome=BusinessOutcome(code="member_not_found"))
    )
    assert turn.disposition == "answered_negative"
    assert turn.ok, "a negative answer is still an answer"
    assert "No member" in turn.message


def test_an_agent_hands_off_rather_than_retrying_when_a_human_holds_the_session() -> None:
    turn = interpret(
        _result(
            RunStatus.NEEDS_HUMAN,
            intervention=InterventionRef(intervention_id="int-1", reason="unknown screen"),
        )
    )
    assert turn.disposition == "handed_off"
    assert not turn.ok


def test_an_agent_surfaces_a_real_failure() -> None:
    turn = interpret(
        _result(
            RunStatus.FAILED,
            error=FailureDetail(
                code=FailureCode.INPUT_VALIDATION_FAILED,
                step_id="enter_member_id",
                message="bad input",
            ),
        )
    )
    assert turn.disposition == "failed"
    assert not turn.ok


# --------------------------------------------------------------------- approval
# Integrity and review are separate axes. These assert they stay separate, and that an approval
# cannot outlive the exact content somebody approved.


def test_a_capability_starts_unapproved(store: CapabilityStore) -> None:
    """Sealing is not reviewing. A freshly compiled artifact is integral and unapproved."""
    entry = store.list()[0]
    assert entry.state == "sealed"
    assert entry.approval is ApprovalState.DRAFT


def test_approval_is_recorded_and_shown(store: CapabilityStore) -> None:
    entry = store.list()[0]
    approvals = ApprovalStore(root=store.root, evals_root=store.root)
    approvals.record(
        ref=entry.ref,
        content_hash=entry.capability.content_hash,
        state=ApprovalState.APPROVED,
        by="reviewer",
    )
    assert store.list()[0].approval is ApprovalState.APPROVED


def test_editing_an_approved_artifact_invalidates_the_approval(store: CapabilityStore) -> None:
    """The property the whole mechanism exists for.

    Approval means "a person reviewed *this* content". The approval record pins a hash so that an
    edit cannot silently inherit the signature. Checked against the *verified* hash rather than
    the declared one, because the declared hash is a line in the same file an editor controls --
    comparing against it let an edited artifact keep reading as approved.
    """
    entry = store.list()[0]
    approvals = ApprovalStore(root=store.root, evals_root=store.root)
    approvals.record(
        ref=entry.ref,
        content_hash=entry.capability.content_hash,
        state=ApprovalState.APPROVED,
        by="reviewer",
    )
    assert store.list()[0].approval is ApprovalState.APPROVED

    path = entry.path
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f"title: {entry.capability.title}", f"title: {entry.capability.title} (edited)", 1
        ),
        encoding="utf-8",
    )

    after = store.list()[0]
    assert after.state == "TAMPERED"
    assert after.approval is ApprovalState.DRAFT, "an edit must not inherit an approval"


def test_revoking_closes_the_gate_again(store: CapabilityStore) -> None:
    """`revoked` rather than back to `draft`.

    "Withdrawn after a problem" and "never reviewed" are different facts, and an operator
    reading the catalog needs to be able to tell them apart.
    """
    entry = store.list()[0]
    approvals = ApprovalStore(root=store.root, evals_root=store.root)
    for state in (ApprovalState.APPROVED, ApprovalState.REVOKED):
        approvals.record(
            ref=entry.ref,
            content_hash=entry.capability.content_hash,
            state=state,
            by="reviewer",
        )
    assert store.list()[0].approval is ApprovalState.REVOKED
    stored = approvals.load(entry.ref)
    assert stored is not None
    assert not stored.permits_unattended_replay(content_hash=entry.capability.content_hash)


def test_a_recommendation_is_refused_without_evidence_for_this_exact_content() -> None:
    """Evidence measured against a different version is not evidence about this one.

    `cua eval` runs a capability bound to a port, and a tenant suite runs it overlaid -- so the
    executed capability hashes differently from the published one. Recording the executed hash
    made every evaluation untraceable to the artifact it was evidence about, and would have let
    `--from-eval` approve on measurements of different content.
    """
    measured = CapabilityEvaluation(
        capability_ref="x@1.0.0", content_hash="sha256:aaa", runs=20, successes=20
    )
    supported, why = CapabilityApproval.recommend(measured)
    assert supported and "20 runs" in why

    thin = CapabilityEvaluation(capability_ref="x@1.0.0", content_hash="sha256:aaa", runs=2)
    assert CapabilityApproval.recommend(thin)[0] is False

    wrong = CapabilityEvaluation(
        capability_ref="x@1.0.0", content_hash="sha256:aaa", runs=50, successes=50, wrong_actions=1
    )
    supported, why = CapabilityApproval.recommend(wrong)
    assert supported is False and "wrong action" in why


def test_catalog_approve_refuses_a_tampered_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: `cua catalog approve --from-eval` trusted the artifact's declared hash.

    A file edited on disk but left claiming its old (still-evaluated) hash would match a prior
    evaluation and record an approval whose evidence never actually measured this content -- the
    same defect class `CapabilityStore.load()` already refuses for replay (see
    `test_an_artifact_edited_in_place_is_refused`), just unreachable from this command's own
    decision point, since it read `capability.content_hash` directly instead of asking whether that
    hash still matched the artifact's bytes.
    """
    monkeypatch.chdir(tmp_path)
    capabilities = tmp_path / "evidence" / "capabilities"
    capabilities.mkdir(parents=True)
    evals = tmp_path / "evidence" / "evals"
    evals.mkdir(parents=True)

    sealed_text = dump_capability(load_capability(FIXTURE.read_text(encoding="utf-8")))
    (capabilities / "savings.yaml").write_text(sealed_text, encoding="utf-8")
    original_hash = load_capability(sealed_text).content_hash

    (evals / "replay_stability.evaluation.json").write_text(
        CapabilityEvaluation(
            capability_ref="corebank.member.savings_balance@1.0.0",
            content_hash=original_hash,
            runs=20,
            successes=20,
        ).model_dump_json(),
        encoding="utf-8",
    )

    # Tamper: edit the sealed file on disk without recomputing its hash -- the exact technique
    # `test_an_artifact_edited_in_place_is_refused` uses for the replay-side version of this bug.
    tampered = sealed_text.replace("savings balance", "CHECKING balance", 1)
    assert tampered != sealed_text, "the edit must actually change the file"
    (capabilities / "savings.yaml").write_text(tampered, encoding="utf-8")

    with pytest.raises(typer.Exit) as raised:
        catalog_approve(
            ref="corebank.member.savings_balance",
            by="reviewer",
            notes="",
            from_eval=True,
        )
    assert raised.value.exit_code == 2

    approval_record = capabilities / "corebank.member.savings_balance@1.0.0.approval.json"
    assert not approval_record.exists(), (
        "a tampered artifact must not end up with an approval record on disk"
    )
