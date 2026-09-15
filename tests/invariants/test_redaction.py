"""AGENTS.md invariant 6: every sink is redacted.

Logs, artifacts, evidence files, screenshots, and outbound model prompts. The test seeds known
secrets and PII, drives a realistic evidence trail, then asserts they appear in **zero** files on
disk.

Written as "scan everything that was written" rather than "check the function redacts" on purpose.
The failure mode that matters is not a broken redactor -- it is a *new sink* someone adds without
routing it through one. Only a filesystem sweep catches that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cua.domain.actor import Actor
from cua.domain.result import FailureCode, FailureDetail, RunResult, RunStatus
from cua.domain.run_record import RunKind, RunRecord
from cua.domain.snapshot import NodeScope, UiNode, UiSnapshot
from cua.evidence.bus import EventType, EvidenceBus
from cua.evidence.record import write_run_record
from cua.policy.config import parse_policy
from cua.policy.redact import Redactor
from cua.policy.secrets import SecretResolver

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"

# Seeded values that must never survive to disk.
SSN = "123-45-6789"
ACCOUNT = "0001234501"
EMAIL = "ada@example.com"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def redactor() -> Redactor:
    config = parse_policy(POLICY.read_text()).redaction
    instance = Redactor(config)
    instance.register_secret(PASSWORD)
    return instance


@pytest.fixture
def bus(tmp_path: Path, redactor: Redactor) -> EvidenceBus:
    return EvidenceBus(
        tmp_path, redactor, run_id="run-redaction", sensitive_keys=frozenset({"password"})
    )


def _leaky_snapshot() -> UiSnapshot:
    """A page carrying exactly the data a real member record would."""
    return UiSnapshot(
        snapshot_id="s1",
        url="http://localhost:8811/member/12345",
        title="MemberDesk",
        nodes=(
            UiNode(node_id="n1", role="cell", name=f"SSN {SSN}", scope=NodeScope(frame="content")),
            UiNode(node_id="n2", role="cell", name=f"Account {ACCOUNT}"),
            UiNode(node_id="n3", role="cell", name=EMAIL),
            UiNode(node_id="n4", role="textbox", name="Password", value=PASSWORD),
        ),
    )


def _all_written_text(root: Path) -> str:
    """Every byte of text this run put on disk."""
    return "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.rglob("*")
        if path.is_file() and path.suffix in (".json", ".jsonl", ".txt", ".yaml", ".log")
    )


def test_no_sink_retains_seeded_secrets(bus: EvidenceBus, tmp_path: Path) -> None:
    """The whole invariant, in one assertion over the filesystem."""
    snapshot = _leaky_snapshot()

    bus.emit(EventType.RUN_START, actor=Actor.AUTOMATION, goal="look up member 12345")
    bus.emit(
        EventType.OBSERVE,
        actor=Actor.AUTOMATION,
        url=snapshot.url,
        note=f"member SSN {SSN}, account {ACCOUNT}, contact {EMAIL}",
        snapshot_ref=bus.save_snapshot(snapshot, "observe"),
    )
    bus.emit(EventType.AUTHORIZE, actor=Actor.AUTOMATION, decision_id="d1", granted=True)
    bus.emit(
        EventType.DISPATCH,
        actor=Actor.AUTOMATION,
        decision_id="d1",
        ok=True,
        password=PASSWORD,
        typed=f"signing on with {PASSWORD}",
    )
    bus.emit(EventType.LLM_CALL, actor=Actor.AUTOMATION, prompt=f"The page shows SSN {SSN}.")

    # The run record is a sink too, and for a long time it was the one this test could not see:
    # every write above goes through the bus, so the assertion below proved "every sink the bus
    # writes is clean" rather than "every sink is clean". `write_run_record` wrote straight to disk.
    write_run_record(
        RunRecord(
            run_id="run-redaction",
            kind=RunKind.REPLAY,
            capability_ref="corebank.member.savings_balance@1.0.0",
            inputs={"member_id": "12345", "password": PASSWORD},
            result=RunResult(
                status=RunStatus.FAILED,
                capability_id="corebank.member.savings_balance",
                capability_version="1.0.0",
                run_id="run-redaction",
                outputs={"ssn_on_file": SSN, "account_number": ACCOUNT},
                error=FailureDetail(
                    code=FailureCode.PRECONDITION_FAILED,
                    step_id="read_balance",
                    message=f"expected the row for {EMAIL}",
                    expected=f"cell containing {ACCOUNT}",
                    observed=f"cell containing SSN {SSN}",
                ),
            ),
        ),
        tmp_path,
        redactor=bus.redactor,
        sensitive_keys=bus.sensitive_keys,
    )

    written = _all_written_text(tmp_path)
    assert written, "the test wrote nothing -- it would pass vacuously"

    for label, secret in (
        ("ssn", SSN),
        ("account number", ACCOUNT),
        ("email", EMAIL),
        ("password", PASSWORD),
    ):
        assert secret not in written, f"{label} leaked into an evidence sink"


def test_redaction_preserves_shape(redactor: Redactor) -> None:
    """Values go, shapes stay.

    A wholesale `***` destroys the evidence trail's usefulness and pushes people toward disabling
    redaction in order to debug -- which is how it ends up disabled in production.
    """
    masked = redactor.value("hunter2", sensitive=True, type_name="string")
    assert masked == "<redacted:string[7]>"
    assert "<redacted:ssn>" in redactor.text(f"SSN {SSN}")


def test_outbound_prompts_are_redacted(redactor: Redactor) -> None:
    """The sink most easily forgotten.

    During discovery a model is shown the page. Regulated data should not leave the process just
    because a model asked for context.
    """
    page = f"Ada Lovelace, account {ACCOUNT}, SSN {SSN}, {EMAIL}"
    assert redactor.is_clean(redactor.text(page))


def test_resolved_credentials_are_scrubbed_by_literal(redactor: Redactor) -> None:
    """No pattern can recognise an arbitrary password, so the resolver registers the literal.

    Asserted through `resolve()` rather than by calling `register_secret` here, because the thing
    worth proving is the *wiring*, not the function. An earlier version of this test did the
    registration itself and passed for months while `register_secret` had no production caller at
    all -- the redactor's `extra_secrets` was empty in every real run.
    """
    from cua.domain.values import SecretRef

    resolver = SecretResolver(overrides={"core.password": PASSWORD}, redactor=redactor)
    value = resolver.resolve(SecretRef.model_validate({"$secret": "core.password"}))

    assert PASSWORD not in redactor.text(f"typed {value} into the field")
    assert not redactor.is_clean(f"typed {PASSWORD} into the field")


def test_every_rig_wires_the_resolver_to_the_redactor() -> None:
    """The guarantee above is only real if production actually connects the two.

    Both rigs are checked: `build_rig` for discovery and replay, and `build_supervised_session` for
    the operator console -- which for a long time constructed no resolver at all, so a capability
    referencing a secret could not have run there.
    """
    import inspect

    from cua.runtime import wiring

    source = inspect.getsource(wiring)
    assert source.count("SecretResolver(redactor=redactor)") == 2, (
        "every SecretResolver in wiring.py must be constructed with the redactor it reports to"
    )
    assert "SecretResolver()" not in source, "a resolver with no redactor scrubs nothing"


def test_snapshots_written_to_disk_are_redacted(bus: EvidenceBus, tmp_path: Path) -> None:
    """Snapshots are the richest evidence and the easiest place to leak: they contain every node's
    name and value verbatim."""
    ref = bus.save_snapshot(_leaky_snapshot(), "detail")
    payload = json.loads((tmp_path / ref).read_text())
    serialized = json.dumps(payload)

    assert SSN not in serialized
    assert ACCOUNT not in serialized
    assert "<redacted:" in serialized


def test_redaction_survives_nested_structures(redactor: Redactor) -> None:
    """Evidence payloads are arbitrary nested JSON, so redaction has to recurse rather than only
    handle top-level strings."""
    nested = {"outer": {"inner": [{"note": f"SSN {SSN}"}, EMAIL]}}
    cleaned = json.dumps(redactor.structure(nested))
    assert SSN not in cleaned
    assert EMAIL not in cleaned


def test_capability_references_survive_redaction(redactor: Redactor) -> None:
    """Over-redaction is a failure too.

    `id@version` looks enough like an email address that a naive pattern eats it, and then every
    run record reports `<redacted:email>` in the one field that says *which capability ran* --
    the question immutable, content-addressed artifacts exist to answer. Redaction has to be tight
    enough to leave structural identifiers legible while still catching real addresses.
    """
    for ref in (
        "corebank.member.savings_balance@1.2.0",
        "memberdesk.savings_balance@1.0.0",
        "corebank.auth.login@10.20.30",
    ):
        assert redactor.text(ref) == ref, f"capability reference was redacted: {ref}"

    # ...and the tightening did not cost us real addresses.
    for address in (EMAIL, "jane.doe+tag@mail.example.co.uk", "ops@bank-internal.org"):
        assert address not in redactor.text(f"contact {address} about it")


def test_a_capability_declaring_an_output_sensitive_masks_it_in_the_record(
    tmp_path: Path, redactor: Redactor
) -> None:
    """`sensitive: true` has to do something, in every sink.

    `config/policy.yaml` promises that fields a capability marks sensitive are "always masked,
    excluded from evidence, and blurred in screenshots". Screenshot blurring read the declaration;
    nothing told the evidence bus about it, so the promise held for pixels and not for text. The
    value here is a plain string no pattern could recognise -- only the declaration identifies it,
    which is the whole reason the declaration exists.
    """
    bus = EvidenceBus(tmp_path, redactor, run_id="r", sensitive_keys=frozenset({"ssn_on_file"}))
    write_run_record(
        RunRecord(
            run_id="r",
            kind=RunKind.REPLAY,
            result=RunResult(
                status=RunStatus.SUCCESS,
                capability_id="c",
                capability_version="1.0.0",
                run_id="r",
                outputs={"ssn_on_file": "AB-9931-QQ", "branch": "Downtown"},
            ),
        ),
        tmp_path,
        redactor=bus.redactor,
        sensitive_keys=bus.sensitive_keys,
    )

    written = (tmp_path / "run_record.json").read_text(encoding="utf-8")
    assert "AB-9931-QQ" not in written, "a declared-sensitive output reached the record"
    assert "<redacted:" in written, "it should be masked by shape, not simply dropped"
    assert "Downtown" in written, "an output nobody declared sensitive must stay readable"
