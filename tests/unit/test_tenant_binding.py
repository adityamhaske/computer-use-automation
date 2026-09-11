"""Tenant overlays specialize a capability without forking it.

The economics of the whole system live here: if automating a flow for one credit union meant
re-recording it for the next two hundred, the product would not work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cua.domain.capability import Capability
from cua.domain.serde import load_capability
from cua.domain.tenant_binding import TenantBinding

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/capabilities/savings_balance.yaml"


@pytest.fixture
def capability() -> Capability:
    return load_capability(FIXTURE.read_text())


@pytest.fixture
def binding() -> TenantBinding:
    """Northgate's overlay: case B divergence only -- they rebranded two row labels."""
    return TenantBinding.model_validate(
        {
            "capability_ref": "corebank.member.savings_balance@1.0.0",
            "tenant": "northgate-fcu",
            "vars": {"base_url": "http://127.0.0.1:8821"},
            "overrides": {
                "read_balance": {
                    "target": {"anchor": {"relation": "adjacent_to", "text": "Savings Bal."}}
                },
                "read_status": {
                    "target": {"anchor": {"relation": "adjacent_to", "text": "Acct Status"}}
                },
            },
        }
    )


def test_overlay_is_four_lines_not_a_re_record(binding: TenantBinding) -> None:
    """The point of the design: a rebranded tenant costs an override, not a discovery run."""
    assert len(binding.overrides) == 2
    assert not binding.recovery_extra


def test_applies_only_declared_overrides(capability: Capability, binding: TenantBinding) -> None:
    effective = binding.apply(capability)

    assert effective.step("read_balance").action.target.anchor.text == "Savings Bal."  # type: ignore[union-attr]
    assert effective.step("read_status").action.target.anchor.text == "Acct Status"  # type: ignore[union-attr]
    # Untouched steps inherit unchanged -- an overlay is a patch, not a replacement.
    assert effective.step("read_as_of").action.target.anchor.text == "As Of"  # type: ignore[union-attr]
    assert [s.id for s in effective.steps] == [s.id for s in capability.steps]


def test_substitutes_tenant_variables(capability: Capability, binding: TenantBinding) -> None:
    """Each tenant runs the same product at its own address."""
    effective = binding.apply(capability)
    assert effective.entrypoint.url_pattern == "http://127.0.0.1:8821/"
    assert "{base_url}" not in effective.entrypoint.url_pattern


def test_effective_capability_has_its_own_content_hash(
    capability: Capability, binding: TenantBinding
) -> None:
    """So a run record names exactly what executed -- tenant specialization included. Otherwise two
    tenants running visibly different flows would report the same version."""
    effective = binding.apply(capability)
    assert effective.hash_is_valid()
    assert effective.content_hash != capability.compute_hash()


def test_effective_capability_is_still_immutable(
    capability: Capability, binding: TenantBinding
) -> None:
    effective = binding.apply(capability)
    assert type(effective) is Capability
    assert effective.model_config["frozen"] is True


def test_overriding_an_unknown_step_fails_closed(
    capability: Capability, binding: TenantBinding
) -> None:
    """Almost always a base version bump that renamed or removed the step.

    Ignoring it would leave the tenant silently running unpatched behaviour while the overlay
    looked applied -- the worst of both.
    """
    broken = binding.model_copy(
        update={"overrides": {"step_that_no_longer_exists": binding.overrides["read_balance"]}}
    )
    with pytest.raises(ValueError, match="unknown step"):
        broken.apply(capability)


def test_binding_is_pinned_to_a_capability_version(
    capability: Capability, binding: TenantBinding
) -> None:
    """A base version bump must be reviewed against the overlay rather than silently inherited."""
    mismatched = binding.model_copy(
        update={"capability_ref": "corebank.member.savings_balance@2.0.0"}
    )
    with pytest.raises(ValueError, match="binding targets"):
        mismatched.apply(capability)


def test_overlay_does_not_mutate_the_base(capability: Capability, binding: TenantBinding) -> None:
    """Shared capabilities are shared. A tenant must not be able to drift one by using it."""
    before = capability.compute_hash()
    binding.apply(capability)
    assert capability.compute_hash() == before
    assert capability.step("read_balance").action.target.anchor.text == "Savings Balance"  # type: ignore[union-attr]
