"""An irreversible step runs unattended only under an explicit, verified, scoped approval.

The gate had one half. `ReplayExecutor` refused an unapproved irreversible capability before the
run, and the policy file said `block_and_escalate` for every irreversible automation step -- so a
capability that cleared both replay gates was refused anyway at dispatch. No approval could ever
permit anything, which made the gate decorative in the other direction.

These drive the real executor, dispatcher and `PolicyEngine` under the shipped `config/policy.yaml`,
against a fake surface with one irreversible control, and assert both halves: nothing irreversible
happens without the grant, and a properly granted step goes through the ordinary chokepoint -- not
around it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cua.domain.action import ActionRisk, Click
from cua.domain.actor import Actor
from cua.domain.approval import ApprovalState, CapabilityApproval
from cua.domain.capability import Capability
from cua.domain.result import FailureCode, RunStatus
from cua.domain.serde import load_capability
from cua.domain.snapshot import UiNode, UiSnapshot
from cua.domain.target import NameMatch, TargetDescriptor
from cua.domain.tenant_binding import AppliedBinding, TenantBinding
from cua.evidence.bus import EventType, EvidenceBus
from cua.policy.authorized import AuthorizedAction
from cua.policy.config import parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.irreversible import IrreversibleGrant
from cua.policy.redact import Redactor
from cua.replay.executor import ReplayExecutor
from cua.runtime.dispatcher import Dispatcher, DispatchStatus
from cua.surfaces.base import ActionResult, SessionInfo
from cua.targeting.resolver import TargetResolver

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"
BASE_URL = "http://localhost:8811"

TRANSFER = """
schema_version: "1.0.0"
id: corebank.member.post_transfer
version: 1.0.0
title: Post a transfer
surface:
  kind: legacy_web
  app: {vendor: acme-core, product: MemberDesk}
entrypoint:
  url_pattern: "{base_url}/"
steps:
  - id: post_transfer
    action:
      type: click
      target: {role: button, name: {value: Transfer Funds, match: exact}}
    risk: irreversible
checkpoint: {assert: node_exists, query: {role: cell, name: Transfer posted}}
policy:
  allowed_domains: ["{base_url}"]
"""


class TransferDesk:
    """A surface with one irreversible control. Clicking it posts the transfer -- once."""

    def __init__(self) -> None:
        self.dispatched: list[AuthorizedAction] = []
        self.posted = False

    def observe(self) -> UiSnapshot:
        nodes = [UiNode(node_id="n1", role="button", name="Transfer Funds")]
        if self.posted:
            nodes.append(UiNode(node_id="n2", role="cell", name="Transfer posted"))
        return UiSnapshot(snapshot_id="s", url=f"{BASE_URL}/", nodes=tuple(nodes))

    def dispatch(self, action: AuthorizedAction) -> ActionResult:
        assert isinstance(action, AuthorizedAction), "the driver must only ever see authorized work"
        self.dispatched.append(action)
        if action.action.type == "click" and action.resolved_node_id == "n1":
            self.posted = True
        return ActionResult(ok=True, duration_ms=1)

    def screenshot(self, *, redact: tuple = ()) -> bytes:
        return b""

    def bounds_for(self, node_ids: tuple[str, ...]) -> dict:
        return {}

    def session_info(self) -> SessionInfo:
        return SessionInfo(session_id="s", driver="fake", url=f"{BASE_URL}/")

    def close(self) -> None:
        return None


@pytest.fixture
def surface(tmp_path: Path) -> tuple[Dispatcher, TransferDesk, EvidenceBus]:
    config = parse_policy(POLICY.read_text())
    bus = EvidenceBus(tmp_path, Redactor(config.redaction), run_id="irreversible")
    desk = TransferDesk()
    dispatcher = Dispatcher(
        driver=desk, policy=PolicyEngine(config), resolver=TargetResolver(), evidence=bus
    )
    return dispatcher, desk, bus


def _base() -> Capability:
    """The artifact as a reviewer approves it: sealed, with `{base_url}` still a parameter."""
    return load_capability(TRANSFER).with_hash()


def _binding() -> TenantBinding:
    return TenantBinding(
        capability_ref="corebank.member.post_transfer@1.0.0",
        tenant="test",
        vars={"base_url": BASE_URL},
    )


def _approved(
    content_hash: str, state: ApprovalState = ApprovalState.APPROVED
) -> CapabilityApproval:
    return CapabilityApproval(
        capability_ref="corebank.member.post_transfer@1.0.0",
        content_hash=content_hash,
        state=state,
        approved_by="reviewer",
    )


def _executor(dispatcher: Dispatcher, bus: EvidenceBus, **grant: object) -> ReplayExecutor:
    return ReplayExecutor(dispatcher=dispatcher, evidence=bus, **grant)  # type: ignore[arg-type]


def _transfer_clicks(desk: TransferDesk) -> list[AuthorizedAction]:
    return [a for a in desk.dispatched if a.action.type == "click"]


# ------------------------------------------------------------------ refused by default


def test_an_unapproved_irreversible_capability_is_refused_before_the_browser_moves(
    surface: tuple[Dispatcher, TransferDesk, EvidenceBus],
) -> None:
    dispatcher, desk, bus = surface
    effective = _binding().apply(_base())

    result = _executor(dispatcher, bus, allow_irreversible=True).run(effective, {})

    assert result.status is RunStatus.FAILED
    assert result.error is not None and result.error.code is FailureCode.POLICY_DENIED
    assert "not approved at this exact content hash" in result.error.message
    assert desk.dispatched == [], "not even the entrypoint navigation may happen"
    assert not desk.posted


def test_an_approval_without_the_callers_opt_in_is_refused(
    surface: tuple[Dispatcher, TransferDesk, EvidenceBus],
) -> None:
    """Two independent gates: a standing approval does not mean *this* invocation asked for it."""
    dispatcher, desk, bus = surface
    base = _base()

    result = _executor(
        dispatcher,
        bus,
        approval=_approved(base.content_hash),
        applied_binding=AppliedBinding(base=base, binding=_binding()),
    ).run(_binding().apply(base), {})

    assert result.error is not None and result.error.code is FailureCode.POLICY_DENIED
    assert "did not opt in" in result.error.message
    assert not desk.posted


@pytest.mark.parametrize(
    "state", [ApprovalState.DRAFT, ApprovalState.REJECTED, ApprovalState.REVOKED]
)
def test_only_an_approved_record_permits_it(
    surface: tuple[Dispatcher, TransferDesk, EvidenceBus], state: ApprovalState
) -> None:
    dispatcher, desk, bus = surface
    base = _base()

    result = _executor(
        dispatcher,
        bus,
        approval=_approved(base.content_hash, state),
        allow_irreversible=True,
        applied_binding=AppliedBinding(base=base, binding=_binding()),
    ).run(_binding().apply(base), {})

    assert result.error is not None and result.error.code is FailureCode.POLICY_DENIED
    assert not desk.posted


# ------------------------------------------------------------------ permitted when granted


def test_an_approved_irreversible_step_runs_through_policy_and_dispatch(
    surface: tuple[Dispatcher, TransferDesk, EvidenceBus],
) -> None:
    """The path that did not exist. Approved content, caller opt-in -- and still the chokepoint."""
    dispatcher, desk, bus = surface
    sealed = load_capability(TRANSFER.replace("{base_url}", BASE_URL)).with_hash()

    result = _executor(
        dispatcher, bus, approval=_approved(sealed.content_hash), allow_irreversible=True
    ).run(sealed, {})

    assert result.status is RunStatus.SUCCESS, result.summary
    clicks = _transfer_clicks(desk)
    assert len(clicks) == 1, "posted exactly once"
    assert clicks[0].risk is ActionRisk.IRREVERSIBLE, "classified by policy, not waved through"
    assert clicks[0].actor is Actor.AUTOMATION

    # Every dispatch is matched to a granted authorization, and the irreversible one records the
    # approval it ran under -- who signed it off, at which exact content.
    assert bus.unauthorized_dispatches() == []
    granted = [
        e
        for e in bus.read_events()
        if e["event"] == EventType.AUTHORIZE.value and e.get("risk") == "irreversible"
    ]
    assert len(granted) == 1 and granted[0]["granted"] is True
    assert "approved by reviewer" in granted[0]["approval"]
    assert sealed.content_hash in granted[0]["approval"]


# ------------------------------------------------------------------ the chokepoint decides


def test_policy_refuses_the_irreversible_dispatch_itself_without_a_valid_grant(
    surface: tuple[Dispatcher, TransferDesk, EvidenceBus],
) -> None:
    """No bypass through the executor: the engine re-verifies at the dispatch.

    A caller that skips `ReplayExecutor` and goes straight to the dispatcher meets the same rule.
    """
    dispatcher, desk, _ = surface
    sealed = load_capability(TRANSFER.replace("{base_url}", BASE_URL)).with_hash()
    click = Click(target=TargetDescriptor(role="button", name=NameMatch(value="Transfer Funds")))
    snapshot = desk.observe()
    session = {"actor": Actor.AUTOMATION, "session_id": "s", "lease_epoch": 1}

    for grant in (
        None,
        IrreversibleGrant(approval=_approved(sealed.content_hash), caller_opt_in=False),
        IrreversibleGrant(approval=_approved("sha256:" + "0" * 64), caller_opt_in=True),
    ):
        refused = dispatcher.execute(
            click,
            snapshot=snapshot,
            capability=sealed,
            declared_risk=ActionRisk.IRREVERSIBLE,
            grant=grant,
            **session,
        )
        assert refused.status is DispatchStatus.DENIED, grant
    assert desk.dispatched == []

    allowed = dispatcher.execute(
        click,
        snapshot=snapshot,
        capability=sealed,
        declared_risk=ActionRisk.IRREVERSIBLE,
        grant=IrreversibleGrant(approval=_approved(sealed.content_hash), caller_opt_in=True),
        **session,
    )
    assert allowed.status is DispatchStatus.OK
    assert len(desk.dispatched) == 1


def test_an_approval_does_not_stretch_to_an_irreversible_control_the_step_did_not_declare(
    surface: tuple[Dispatcher, TransferDesk, EvidenceBus],
) -> None:
    """The reviewer approved a step declared `safe`. The control turned out to be "Transfer Funds".

    That surprise is exactly what the lexicon signal exists to catch, and an approval of content
    that never declared an irreversible step cannot be what permits one.
    """
    dispatcher, desk, _ = surface
    sealed = load_capability(TRANSFER.replace("{base_url}", BASE_URL)).with_hash()

    outcome = dispatcher.execute(
        Click(target=TargetDescriptor(role="button", name=NameMatch(value="Transfer Funds"))),
        snapshot=desk.observe(),
        actor=Actor.AUTOMATION,
        session_id="s",
        lease_epoch=1,
        capability=sealed,
        declared_risk=ActionRisk.SAFE,
        grant=IrreversibleGrant(approval=_approved(sealed.content_hash), caller_opt_in=True),
    )

    assert outcome.status is DispatchStatus.DENIED
    assert "not declared at that tier" in outcome.message
    assert desk.dispatched == []


def test_a_tampered_capability_is_refused_despite_its_declared_hash(
    surface: tuple[Dispatcher, TransferDesk, EvidenceBus],
) -> None:
    """Edited after sealing, keeping the old `content_hash` line the approval matches."""
    dispatcher, desk, bus = surface
    sealed = load_capability(TRANSFER.replace("{base_url}", BASE_URL)).with_hash()
    tampered = sealed.model_copy(update={"title": "Post a transfer (edited after review)"})
    assert tampered.content_hash == sealed.content_hash and not tampered.hash_is_valid()

    result = _executor(
        dispatcher, bus, approval=_approved(sealed.content_hash), allow_irreversible=True
    ).run(tampered, {})

    assert result.error is not None and result.error.code is FailureCode.POLICY_DENIED
    assert not desk.posted


# ------------------------------------------------------------------ {base_url} + approval


def test_an_approval_of_a_base_url_capability_covers_its_bound_form(
    surface: tuple[Dispatcher, TransferDesk, EvidenceBus],
) -> None:
    """The regression. Every shipped capability binds `{base_url}` at run time, and binding reseals
    under a new hash -- so an approval pinned to the reviewed base could never match what ran.

    Approval stays pinned to the immutable base; the gate re-derives the executing capability from
    base + binding and checks the approval against the base.
    """
    dispatcher, desk, bus = surface
    base = _base()
    effective = _binding().apply(base)
    assert effective.content_hash != base.content_hash, "binding reseals; that is not the bug"

    result = _executor(
        dispatcher,
        bus,
        approval=_approved(base.content_hash),
        allow_irreversible=True,
        applied_binding=AppliedBinding(base=base, binding=_binding()),
    ).run(effective, {})

    assert result.status is RunStatus.SUCCESS, result.summary
    assert len(_transfer_clicks(desk)) == 1
    granted = next(
        e
        for e in bus.read_events()
        if e["event"] == EventType.AUTHORIZE.value and e.get("risk") == "irreversible"
    )
    assert "via tenant binding 'test'" in granted["approval"]
    assert base.content_hash in granted["approval"]


def test_without_the_binding_the_base_approval_does_not_reach_the_bound_capability(
    surface: tuple[Dispatcher, TransferDesk, EvidenceBus],
) -> None:
    """Still fail-closed: the approval never silently follows a hash change."""
    dispatcher, desk, bus = surface
    base = _base()

    result = _executor(
        dispatcher, bus, approval=_approved(base.content_hash), allow_irreversible=True
    ).run(_binding().apply(base), {})

    assert result.error is not None and result.error.code is FailureCode.POLICY_DENIED
    assert not desk.posted


def test_a_capability_altered_after_binding_does_not_inherit_the_base_approval(
    surface: tuple[Dispatcher, TransferDesk, EvidenceBus],
) -> None:
    """Resealed so its own hash is valid -- but it is not what base + binding produce."""
    dispatcher, desk, bus = surface
    base = _base()
    altered = (
        _binding().apply(base).model_copy(update={"title": "something else", "content_hash": ""})
    ).with_hash()
    assert altered.hash_is_valid()

    result = _executor(
        dispatcher,
        bus,
        approval=_approved(base.content_hash),
        allow_irreversible=True,
        applied_binding=AppliedBinding(base=base, binding=_binding()),
    ).run(altered, {})

    assert result.error is not None and result.error.code is FailureCode.POLICY_DENIED
    assert "changed after binding" in result.error.message
    assert not desk.posted


def test_a_tampered_base_is_refused_through_the_binding(
    surface: tuple[Dispatcher, TransferDesk, EvidenceBus],
) -> None:
    """The base was edited after it was approved; binding it produces a validly sealed effective
    capability, which is precisely why the base's own integrity has to be checked too."""
    dispatcher, desk, bus = surface
    base = _base()
    tampered = base.model_copy(update={"title": "edited after review"})
    effective = _binding().apply(tampered)
    assert effective.hash_is_valid()

    result = _executor(
        dispatcher,
        bus,
        approval=_approved(base.content_hash),
        allow_irreversible=True,
        applied_binding=AppliedBinding(base=tampered, binding=_binding()),
    ).run(effective, {})

    assert result.error is not None and result.error.code is FailureCode.POLICY_DENIED
    assert "does not match its own content hash" in result.error.message
    assert not desk.posted


def test_a_binding_that_changes_behaviour_is_not_covered_by_the_base_approval(
    surface: tuple[Dispatcher, TransferDesk, EvidenceBus],
) -> None:
    """Filling `{base_url}` changes where the reviewed flow runs. Retargeting a step changes what
    it does, and nobody reviewed that."""
    dispatcher, desk, bus = surface
    base = _base()
    overriding = _binding().model_copy(
        update={
            "overrides": TenantBinding.model_validate(
                {
                    "capability_ref": base.ref,
                    "tenant": "test",
                    "overrides": {"post_transfer": {"target": {"hints": {"css": "#other"}}}},
                }
            ).overrides
        }
    )

    result = _executor(
        dispatcher,
        bus,
        approval=_approved(base.content_hash),
        allow_irreversible=True,
        applied_binding=AppliedBinding(base=base, binding=overriding),
    ).run(overriding.apply(base), {})

    assert result.error is not None and result.error.code is FailureCode.POLICY_DENIED
    assert "overrides steps" in result.error.message
    assert not desk.posted


# ------------------------------------------------------------------ the policy file


def test_a_misspelled_disposition_fails_at_load() -> None:
    """As free text, an unknown disposition fell through `authorize` to *allow*."""
    text = POLICY.read_text().replace(
        "irreversible: allow_if_approved", "irreversible: allow_if_aproved"
    )
    assert "allow_if_aproved" in text
    with pytest.raises(ValidationError):
        parse_policy(text)
