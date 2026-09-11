"""Cross-tenant reuse, measured against two real deployments of the same vendor product.

This is the test behind the claim in REPORT.md §4, and it is deliberately structured to be
*falsifiable*. An artifact recorded against the base tenant is resolved against Variant B, which
carries two different kinds of divergence:

    Case A  markup churn   -- label preserved, CSS class and form field name changed
    Case B  rebranding     -- label itself changed

Case A must succeed with no overlay: that is what "this system does not depend on CSS selectors"
means operationally. If the system had quietly become selector-coupled, this test fails.

Case B must *fail closed* without an overlay and succeed with one. That asymmetry is the design
position: inferring that "Savings Bal." means "Savings Balance" is a guess, and a system that
guesses which row holds a balance eventually reads the wrong one.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from apps.mock_bank.server import VALID_PW, VALID_USER, create_app

from cua.domain.capability import Capability
from cua.domain.serde import load_capability
from cua.domain.target import ResolutionStrategy
from cua.domain.tenant_binding import TenantBinding
from cua.surfaces.playwright_cdp.driver import PlaywrightCdpDriver
from cua.targeting.resolver import TargetResolutionError, TargetResolver

pytestmark = [pytest.mark.browser, pytest.mark.slow]

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/capabilities/savings_balance.yaml"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Server:
    def __init__(self, variant: str) -> None:
        self.port = _free_port()
        self._server = uvicorn.Server(
            uvicorn.Config(create_app(variant), host="127.0.0.1", port=self.port, log_level="error")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> _Server:
        self._thread.start()
        for _ in range(100):
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("mock app did not start")

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@pytest.fixture(scope="module")
def base_app() -> Iterator[_Server]:
    with _Server("base") as server:
        yield server


@pytest.fixture(scope="module")
def tenant_b_app() -> Iterator[_Server]:
    with _Server("variant_b") as server:
        yield server


@pytest.fixture
def driver() -> Iterator[PlaywrightCdpDriver]:
    instance = PlaywrightCdpDriver(headless=True)
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def capability() -> Capability:
    """The artifact recorded against the BASE tenant. Never re-recorded for tenant B."""
    return load_capability(FIXTURE.read_text())


def _goto(driver: PlaywrightCdpDriver, base_url: str, path: str) -> None:
    """Sign in, then drive the *content frame* to `path`.

    Deliberately faithful to how the app is really used: the operator works inside the frameset, so
    the content frame navigates while the top-level URL stays at the shell. Loading `path` at the
    top level instead would produce a page no operator ever sees, and the artifact's
    `scope: {frame: content}` would not apply to it.
    """
    page = driver.page
    page.goto(f"{base_url}/login")
    page.fill('input[name="user"]', VALID_USER)
    page.fill('input[name="pw"]', VALID_PW)
    page.click('input[type="submit"]')
    page.wait_for_load_state()

    page.goto(f"{base_url}/")
    page.wait_for_load_state()
    content = page.frame(name="content")
    assert content is not None, "the frameset did not produce a content frame"
    content.goto(f"{base_url}{path}")
    page.wait_for_load_state()


def _target(capability: Capability, step_id: str):
    step = capability.step(step_id)
    assert step is not None
    return step.action.target  # type: ignore[union-attr]


# ------------------------------------------------ baseline: the home tenant


def test_artifact_resolves_against_its_own_tenant(
    driver: PlaywrightCdpDriver, base_app: _Server, capability: Capability
) -> None:
    """Control: the recorded artifact works where it was recorded, with no drift."""
    _goto(driver, base_app.url, "/search")
    resolver = TargetResolver()

    resolution, drifted = resolver.resolve_with_drift(
        _target(capability, "enter_member_id"), driver.observe()
    )
    assert resolution.strategy is ResolutionStrategy.SEMANTIC_EXACT
    assert not drifted


# --------------------------------- case A: markup churn, no overlay needed


def test_case_a_survives_dead_selectors(
    driver: PlaywrightCdpDriver, tenant_b_app: _Server, capability: Capability
) -> None:
    """The falsifiable form of "this system does not depend on CSS selectors".

    Tenant B renamed the form field (`memno` -> `member_num`) and restyled it (`frmfld` ->
    `ng-inp`), so the artifact's cached `hints.css` cannot resolve anything here. The accessible
    name survived, so the control is found anyway -- by semantics, at the top rung, with no drift
    and no overlay.
    """
    _goto(driver, tenant_b_app.url, "/search")
    snapshot = driver.observe()
    target = _target(capability, "enter_member_id")

    # The cached selector really is dead on this tenant.
    assert target.hints.get("css") == 'input[name="memno"]'
    assert not [n for n in snapshot.nodes if n.hints.get("css") == target.hints.get("css")]

    resolution, drifted = TargetResolver().resolve_with_drift(target, snapshot)
    assert resolution.strategy is ResolutionStrategy.SEMANTIC_EXACT
    assert resolution.node.role == "textbox"
    assert not drifted, "a dead selector is not drift -- the hint was never the identity"


# ----------------------------- case B: rebranding, fails closed then works


def test_case_b_fails_closed_without_an_overlay(
    driver: PlaywrightCdpDriver, tenant_b_app: _Server, capability: Capability
) -> None:
    """Tenant B relabelled the row, which defeats structural anchoring too -- the anchor text *is*
    the label.

    The designed behaviour is refusal. Guessing that "Savings Bal." means "Savings Balance" is
    precisely the inference that ends with an automation reading the wrong row of a member's
    accounts.
    """
    _goto(driver, tenant_b_app.url, "/member/12345")
    snapshot = driver.observe()

    with pytest.raises(TargetResolutionError) as raised:
        TargetResolver().resolve(_target(capability, "read_balance"), snapshot)

    assert "no candidate matched" in raised.value.reason
    assert raised.value.strategies_tried, "the failure must say which rungs were attempted"


def test_case_b_resolves_with_a_four_line_overlay(
    driver: PlaywrightCdpDriver, tenant_b_app: _Server, capability: Capability
) -> None:
    """The multi-tenant economics, demonstrated.

    A rebranded tenant costs an overlay naming the new label -- not a re-recorded capability, and
    not a fork. The same base artifact serves both institutions.
    """
    binding = TenantBinding.model_validate(
        {
            "capability_ref": capability.ref,
            "tenant": "northgate-fcu",
            "vars": {"base_url": tenant_b_app.url},
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
    effective = binding.apply(capability)

    _goto(driver, tenant_b_app.url, "/member/12345")
    snapshot = driver.observe()

    resolution = TargetResolver().resolve(_target(effective, "read_balance"), snapshot)
    assert resolution.strategy is ResolutionStrategy.STRUCTURAL_ANCHOR
    assert resolution.node.name == "$4,210.55", "the overlay must reach the same real value"

    status = TargetResolver().resolve(_target(effective, "read_status"), snapshot)
    assert status.node.name == "Active"


def test_overlay_leaves_untouched_steps_alone(
    driver: PlaywrightCdpDriver, tenant_b_app: _Server, capability: Capability
) -> None:
    """An overlay is a patch, not a replacement: steps that did not diverge still resolve by the
    base artifact's own description."""
    binding = TenantBinding.model_validate(
        {
            "capability_ref": capability.ref,
            "tenant": "northgate-fcu",
            "vars": {"base_url": tenant_b_app.url},
            "overrides": {
                "read_balance": {
                    "target": {"anchor": {"relation": "adjacent_to", "text": "Savings Bal."}}
                }
            },
        }
    )
    effective = binding.apply(capability)

    _goto(driver, tenant_b_app.url, "/search")
    resolution = TargetResolver().resolve(_target(effective, "enter_member_id"), driver.observe())
    assert resolution.strategy is ResolutionStrategy.SEMANTIC_EXACT


# ------------------------------------------------------------ determinism


def test_resolution_is_reproducible_across_tenants(
    driver: PlaywrightCdpDriver, tenant_b_app: _Server, capability: Capability
) -> None:
    _goto(driver, tenant_b_app.url, "/search")
    snapshot = driver.observe()
    target = _target(capability, "enter_member_id")
    resolver = TargetResolver()

    results = [resolver.resolve(target, snapshot) for _ in range(10)]
    assert len({r.node.node_id for r in results}) == 1
    assert len({r.strategy for r in results}) == 1
