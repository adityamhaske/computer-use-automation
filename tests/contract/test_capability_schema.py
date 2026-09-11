"""The capability artifact honours its contract.

The artifact schema is what this project is graded on first, so its properties are asserted rather
than assumed: it round-trips, it is content-addressed, it is immutable, it catches the authoring
mistakes that would otherwise surface as a confusing mid-replay failure, and it doubles as the
agent-facing tool contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from cua.domain.action import ActionRisk
from cua.domain.capability import Capability
from cua.domain.serde import dump_capability, load_capability, to_yaml

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/capabilities/savings_balance.yaml"


@pytest.fixture
def capability() -> Capability:
    return load_capability(FIXTURE.read_text())


# ------------------------------------------------------------------ shape


def test_reference_artifact_validates(capability: Capability) -> None:
    assert capability.ref == "corebank.member.savings_balance@1.0.0"
    assert [s.id for s in capability.steps] == [
        "enter_member_id",
        "submit_search",
        "read_balance",
        "read_status",
        "read_as_of",
    ]


def test_declares_the_three_condition_classes_separately(capability: Capability) -> None:
    """Business outcomes, recovery rules and the success checkpoint are distinct concepts.

    The brief names conflating the first with failure as the most common design mistake in this
    problem, so the schema keeps them apart structurally rather than by convention.
    """
    assert {o.code for o in capability.outcomes} == {
        "member_not_found",
        "no_savings_account",
        "permission_denied",
    }
    assert {r.id for r in capability.recovery} == {"transient_load", "session_expired"}
    assert capability.checkpoint is not None


def test_every_recovery_rule_is_bounded(capability: Capability) -> None:
    """Unbounded recovery turns one transient 502 into a thousand retries against a struggling
    core banking system. The schema makes max_attempts mandatory."""
    for rule in capability.recovery:
        assert 1 <= rule.max_attempts <= 10


def test_no_target_uses_a_css_selector_as_its_identity(capability: Capability) -> None:
    """AGENTS.md invariant 3. CSS may appear only as a `hints` cache, never as identity.

    This is what keeps the artifact portable to a surface where no CSS exists.
    """
    for step in capability.steps:
        target = getattr(step.action, "target", None)
        if target is None:
            continue
        assert target.role, f"{step.id}: every target must carry a semantic role"
        assert target.name or target.anchor or target.ordinal is not None, (
            f"{step.id}: a target needs a semantic handle (name, anchor or ordinal), not just hints"
        )


# ------------------------------------------------------------- round trip


def test_round_trips_through_yaml(capability: Capability) -> None:
    reparsed = load_capability(dump_capability(capability))
    assert reparsed.model_dump() == capability.with_hash().model_dump()


def test_serialization_is_stable(capability: Capability) -> None:
    """Two dumps of the same artifact are identical -- otherwise every review would show spurious
    diffs and real changes would be hard to spot."""
    assert to_yaml(capability) == to_yaml(capability)


# --------------------------------------------------------- content address


def test_hash_is_deterministic(capability: Capability) -> None:
    assert capability.compute_hash() == capability.compute_hash()
    assert capability.compute_hash().startswith("sha256:")


def test_hash_excludes_itself(capability: Capability) -> None:
    """Otherwise sealing an artifact would change the thing being hashed."""
    sealed = capability.with_hash()
    assert sealed.hash_is_valid()
    assert sealed.compute_hash() == capability.compute_hash()


def test_tampering_is_detected(capability: Capability) -> None:
    """An artifact is an executable contract. Running a modified one while recording a version
    that no longer matches its content would make the audit trail a fiction."""
    sealed = capability.with_hash()
    tampered = sealed.model_copy(update={"title": "Something else"})
    assert not tampered.hash_is_valid()

    with pytest.raises(ValueError, match="content hash mismatch"):
        load_capability(to_yaml(tampered))


def test_unsealed_draft_is_accepted(capability: Capability) -> None:
    """A hand-authored artifact with no hash is a draft, not a tampered document."""
    assert load_capability(FIXTURE.read_text()).content_hash == ""


# ------------------------------------------------------------ immutability


def test_capability_is_frozen(capability: Capability) -> None:
    with pytest.raises(ValidationError):
        capability.title = "mutated"  # type: ignore[misc]


FORBIDDEN_RUNTIME_FIELDS = {
    "stability",
    "stability_score",
    "approval",
    "approval_state",
    "last_run",
    "last_run_at",
    "run_count",
    "runs",
    "success_rate",
    "drift_score",
    "evaluation",
    "tenant",
}


def test_capability_carries_no_runtime_telemetry() -> None:
    """AGENTS.md invariant 8, enforced mechanically.

    A definition that accumulates run telemetry stops being reviewable -- a diff between versions
    fills with counters instead of behaviour -- and "which version produced that run?" stops being
    answerable. Telemetry lives in RunRecord / CapabilityEvaluation / CapabilityApproval instead.

    This test is the thing that notices when someone adds `stability` back "just for convenience".
    """
    leaked = FORBIDDEN_RUNTIME_FIELDS & set(Capability.model_fields)
    assert not leaked, (
        f"Capability gained mutable runtime field(s) {sorted(leaked)}. "
        "These belong in a sibling document -- see ADR 0002."
    )


def test_provenance_is_a_reference_not_a_transcript(capability: Capability) -> None:
    """Brief §3.2: the artifact must be decoupled from the raw model transcript."""
    fields = set(type(capability.provenance).model_fields)
    assert "transcript_ref" in fields
    assert "transcript" not in fields and "messages" not in fields


# ------------------------------------------------------------- validation
# Each of these would otherwise surface as a confusing failure in the middle of a replay.


def _mutate(raw: dict, **changes: object) -> dict:
    return {**raw, **changes}


@pytest.fixture
def raw() -> dict:
    return yaml.safe_load(FIXTURE.read_text().replace("  triggers:", "  triggers:"))


def test_rejects_undeclared_input_reference(raw: dict) -> None:
    raw["steps"][0]["action"]["value"] = {"$input": "not_declared"}
    with pytest.raises(ValidationError, match="undeclared input"):
        Capability.model_validate(raw)


def test_rejects_extract_into_undeclared_output(raw: dict) -> None:
    raw["steps"][2]["action"]["into"] = "not_an_output"
    with pytest.raises(ValidationError, match="undeclared output"):
        Capability.model_validate(raw)


def test_rejects_output_sourced_from_unknown_step(raw: dict) -> None:
    raw["outputs"][0]["source"] = {"step": "no_such_step"}
    with pytest.raises(ValidationError, match="unknown step"):
        Capability.model_validate(raw)


def test_rejects_duplicate_step_ids(raw: dict) -> None:
    raw["steps"][1]["id"] = raw["steps"][0]["id"]
    with pytest.raises(ValidationError, match="unique"):
        Capability.model_validate(raw)


def test_rejects_non_semver_version(raw: dict) -> None:
    raw["version"] = "1.0"
    with pytest.raises(ValidationError, match="semver"):
        Capability.model_validate(raw)


def test_rejects_unknown_fields(raw: dict) -> None:
    """extra="forbid" everywhere: a typo in a hand-authored artifact should fail loudly, not be
    silently ignored and then not do what the author expected."""
    raw["chekpoint"] = raw["checkpoint"]
    with pytest.raises(ValidationError):
        Capability.model_validate(raw)


# ------------------------------------------------------- the tool contract


def test_exports_an_agent_callable_tool_schema(capability: Capability) -> None:
    """The artifact IS the tool contract -- there is no second source of truth to drift."""
    schema = capability.tool_schema()

    assert schema["name"] == "corebank_member_savings_balance"
    assert schema["input_schema"]["required"] == ["member_id"]
    assert schema["input_schema"]["properties"]["member_id"]["pattern"] == "^[0-9]{4,10}$"
    assert schema["input_schema"]["additionalProperties"] is False

    assert schema["output_schema"]["properties"]["savings_balance"]["format"] == "money"
    assert schema["output_schema"]["properties"]["as_of"]["format"] == "date"

    # A calling agent must be told which non-failure answers it may receive.
    assert {o["code"] for o in schema["outcomes"]} == {
        "member_not_found",
        "no_savings_account",
        "permission_denied",
    }


def test_reports_its_riskiest_action(capability: Capability) -> None:
    """Drives the approval gate: a capability that only reads need not be gated like one that
    moves money."""
    assert capability.max_risk is ActionRisk.ELEVATED


# ------------------------------------------------------------- yaml safety


def test_loader_does_not_coerce_ambiguous_yaml_scalars() -> None:
    """YAML 1.1 resolves bare on/off/yes/no to booleans -- in keys *and* values.

    In a document humans hand-edit that is a landmine: an outcome code of `no` silently becomes
    False, and stdlib SafeLoader will even collapse distinct keys into one. Only true/false are
    booleans here.
    """
    probe = "{on: 1, off: 2, yes: 3, no: 4, true: 5}"

    # stdlib collapses on/yes/true into True and off/no into False -- five keys become two,
    # silently, with no error and no warning.
    assert len(yaml.safe_load(probe)) == 2

    from cua.domain.serde import _StrictLoader

    parsed = yaml.load(probe, Loader=_StrictLoader)
    assert set(parsed) == {"on", "off", "yes", "no", True}
