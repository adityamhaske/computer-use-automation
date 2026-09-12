"""The error taxonomy, one row per condition.

This is the test that proves the taxonomy is real rather than described. Each row names a condition
the application can actually produce and the terminal status it must map to. If a new condition
cannot be given a row here, it has not been classified yet -- which is the point.

The distinction being defended is the one the brief calls the most common design mistake in this
problem: "no such member" is a legitimate answer that exits 0, not a crash.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from apps.mock_bank.server import VALID_PW, VALID_USER, create_app

from cua.domain.capability import Capability
from cua.domain.result import FailureCode, RunStatus
from cua.domain.serde import load_capability
from cua.domain.target import Anchor, AnchorRelation
from cua.domain.tenant_binding import TenantBinding
from cua.evidence.bus import EvidenceBus
from cua.policy.config import parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.redact import Redactor
from cua.replay.executor import ReplayExecutor
from cua.runtime.capture import FailureCapture
from cua.runtime.dispatcher import Dispatcher
from cua.surfaces.playwright_cdp.driver import PlaywrightCdpDriver
from cua.targeting.resolver import TargetResolver

pytestmark = [pytest.mark.browser, pytest.mark.slow]

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/capabilities/savings_balance.yaml"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def app() -> Iterator[tuple[str, int]]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app("base"), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", port
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def capability(app: tuple[str, int]) -> Capability:
    """The reference artifact, bound to this test's ephemeral port via a tenant overlay.

    Using the overlay rather than editing the fixture is deliberate: binding a shared capability to
    a deployment is exactly what `TenantBinding` is for, so the test exercises that path too.
    """
    base_url, _ = app
    binding = TenantBinding.model_validate(
        {
            "capability_ref": "corebank.member.savings_balance@1.0.0",
            "tenant": "test",
            "vars": {"base_url": base_url},
        }
    )
    return binding.apply(load_capability(FIXTURE.read_text()))


@pytest.fixture
def replay(app: tuple[str, int], tmp_path: Path) -> Iterator[ReplayExecutor]:
    base_url, port = app
    config = parse_policy(POLICY.read_text())
    config = config.model_copy(
        update={
            "allowlist": config.allowlist.model_copy(
                update={"domains": (*config.allowlist.domains, f"127.0.0.1:{port}")}
            )
        }
    )
    redactor = Redactor(config.redaction)
    bus = EvidenceBus(tmp_path, redactor, run_id="fault-matrix")
    driver = PlaywrightCdpDriver(headless=True)

    page = driver.page
    page.goto(f"{base_url}/login")
    page.fill('input[name="user"]', VALID_USER)
    page.fill('input[name="pw"]', VALID_PW)
    page.click('input[type="submit"]')
    page.wait_for_load_state()

    dispatcher = Dispatcher(
        driver=driver,
        policy=PolicyEngine(config),
        # No vision rung: replay is deterministic, and pixel coordinates are not.
        resolver=TargetResolver(allow_vision=False),
        evidence=bus,
    )
    try:
        yield ReplayExecutor(
            dispatcher=dispatcher,
            evidence=bus,
            capture=FailureCapture(driver=driver, evidence=bus),
        )
    finally:
        driver.close()


def arm(app: tuple[str, int], fault: str, count: int = 1) -> None:
    httpx.post(f"{app[0]}/_control/arm", json={"fault": fault, "count": count}, timeout=5)


def reset(app: tuple[str, int]) -> None:
    httpx.post(f"{app[0]}/_control/reset", timeout=5)


# ============================================================ the happy path


def test_success_returns_typed_outputs(
    replay: ReplayExecutor, capability: Capability, app: tuple[str, int]
) -> None:
    """Replayed with an input the discovery run never saw. If the value were baked in, this would
    still pass while being completely wrong, so the assertion is on 67890's balance."""
    reset(app)
    result = replay.run(capability, {"member_id": "67890"})

    assert result.status is RunStatus.SUCCESS, result.summary
    assert result.exit_code == 0
    assert result.outputs["savings_balance"] == "$18,730.00"
    assert result.outputs["account_status"] == "Active"
    assert result.outputs["as_of"]


def test_replay_uses_no_model(
    replay: ReplayExecutor, capability: Capability, app: tuple[str, int]
) -> None:
    """AGENTS.md invariant 1, at runtime rather than as an import rule.

    The import contract cannot catch a lazy import inside a function; this can.
    """
    import cua.agent.llm as llm_module

    def explode(*_: object, **__: object) -> None:
        raise AssertionError("replay reached the model")

    original = llm_module.OpenRouterLlm.complete
    llm_module.OpenRouterLlm.complete = explode  # type: ignore[method-assign]
    try:
        reset(app)
        assert replay.run(capability, {"member_id": "12345"}).status is RunStatus.SUCCESS
    finally:
        llm_module.OpenRouterLlm.complete = original  # type: ignore[method-assign]


# ====================================================== BUSINESS OUTCOMES
# Legitimate answers the caller needs. Every one of these exits 0.


@pytest.mark.parametrize(
    ("member_id", "expected_code"),
    [
        ("99999", "member_not_found"),
        ("13579", "no_savings_account"),
        ("55555", "permission_denied"),
    ],
)
def test_business_outcomes_are_answers_not_failures(
    replay: ReplayExecutor,
    capability: Capability,
    app: tuple[str, int],
    member_id: str,
    expected_code: str,
) -> None:
    """The mistake the brief singles out.

    Conflating these with failure turns a routine result into a 2am page, and trains everyone to
    ignore the alert that also fires for real defects.
    """
    reset(app)
    result = replay.run(capability, {"member_id": member_id})

    assert result.status is RunStatus.BUSINESS_OUTCOME, result.summary
    assert result.exit_code == 0, "a business outcome is a successful execution"
    assert result.outcome is not None
    assert result.outcome.code == expected_code
    assert result.error is None


# ========================================================= RECOVERABLE


def test_a_transient_failure_is_recovered(
    replay: ReplayExecutor, capability: Capability, app: tuple[str, int]
) -> None:
    """One 502, then success. The declared remedy reloads and the run continues."""
    reset(app)
    arm(app, "transient_load", count=1)
    result = replay.run(capability, {"member_id": "12345"})

    assert result.status is RunStatus.SUCCESS, result.summary
    assert result.recovery_attempts >= 1, "the recovery rule should have fired"


def test_a_transient_that_never_clears_is_bounded(
    replay: ReplayExecutor, capability: Capability, app: tuple[str, int]
) -> None:
    """Unbounded recovery is how one transient 502 becomes a thousand retries against a core
    banking system that is already struggling. `max_attempts` is mandatory in the schema."""
    reset(app)
    arm(app, "transient_load", count=-1)
    result = replay.run(capability, {"member_id": "12345"})

    assert result.status in (RunStatus.FAILED, RunStatus.NEEDS_HUMAN), result.summary
    if result.error:
        assert result.error.code is FailureCode.RECOVERY_EXHAUSTED
    assert result.recovery_attempts <= 4, "attempts must be bounded by the declared maximum"
    reset(app)


# ======================================================= UNEXPECTED STATE


def test_an_undeclared_screen_fails_closed(
    replay: ReplayExecutor, capability: Capability, app: tuple[str, int]
) -> None:
    """The category most systems lack.

    A maintenance notice this capability never declared appears mid-flow. It is styled as a routine
    interstitial precisely because the dangerous case is the one that looks harmless -- and the
    system must refuse to click through it rather than deciding it is probably fine.
    """
    reset(app)
    arm(app, "undeclared_dialog", count=3)
    result = replay.run(capability, {"member_id": "12345"})
    reset(app)

    assert result.status is RunStatus.NEEDS_HUMAN, result.summary
    assert result.exit_code == 2
    assert result.intervention is not None
    assert result.outputs == {}, "nothing may be reported from a screen we do not understand"


# ====================================================== INPUT VALIDATION


def test_bad_input_is_caught_before_the_browser(
    replay: ReplayExecutor, capability: Capability, app: tuple[str, int]
) -> None:
    """The caller's bug, distinguished from an answer about a member.

    Returning "member not found" for a malformed id would be actively misleading: it says something
    about the institution's records when the truth is about the request.
    """
    reset(app)
    result = replay.run(capability, {"member_id": "not-a-number"})

    assert result.status is RunStatus.FAILED
    assert result.error is not None
    assert result.error.code is FailureCode.INPUT_VALIDATION_FAILED
    assert result.steps_executed == 0


# ============================================================== DEBUGGING


def test_a_failure_says_what_was_expected_and_what_was_seen(
    replay: ReplayExecutor, capability: Capability, app: tuple[str, int]
) -> None:
    """§3.3: a failure must be debuggable without reproducing it."""
    reset(app)
    result = replay.run(capability, {"member_id": "xx"})

    assert result.error is not None
    assert result.error.message
    assert "pattern" in result.error.message


def test_every_run_records_its_drift_and_strategy_mix(
    replay: ReplayExecutor, capability: Capability, app: tuple[str, int]
) -> None:
    """The early warning: which rung resolved each target, and how often resolution fell below the
    rung recorded at discovery."""
    reset(app)
    result = replay.run(capability, {"member_id": "12345"})

    assert result.strategy_mix, "each resolution must record the rung that won"
    assert result.drift_score == 0.0, "the reference artifact should not drift on its own app"


# ============================================================ DETERMINISM


def test_repeated_replays_produce_identical_decisions(
    replay: ReplayExecutor, capability: Capability, app: tuple[str, int]
) -> None:
    """Same artifact, same inputs, same state, same decisions.

    If two replays can disagree, a divergence is unattributable -- it could be a UI change or a coin
    flip, and those demand opposite responses.
    """
    reset(app)
    runs = [replay.run(capability, {"member_id": "12345"}) for _ in range(3)]

    assert {run.status for run in runs} == {RunStatus.SUCCESS}
    assert len({tuple(sorted(run.outputs.items())) for run in runs}) == 1
    assert len({tuple(sorted(run.strategy_mix.items())) for run in runs}) == 1


# ============================================ the budgets the artifact declares

# Three bounds sat in the schema and in config/policy.yaml, parsed and read by nothing. A reviewer
# opening the artifact and seeing `policy: {max_steps: 40, max_duration_ms: 120000}` reasonably
# concluded replay was bounded. It was not. These assert that it now is.


def test_a_capability_declaring_more_steps_than_its_budget_is_refused(
    replay: ReplayExecutor, capability: Capability
) -> None:
    """Caught before the browser moves -- the cheapest place to reject a contract violation."""
    over = capability.model_copy(
        update={"policy": capability.policy.model_copy(update={"max_steps": 2})}
    )
    result = replay.run(over, {"member_id": "12345"})

    assert result.status is RunStatus.FAILED
    assert result.error is not None
    assert result.error.code is FailureCode.POLICY_DENIED
    assert "allows 2" in result.error.message
    assert result.steps_executed == 0, "nothing should have run"


def test_a_run_that_outlives_its_declared_duration_is_stopped(
    replay: ReplayExecutor, capability: Capability
) -> None:
    """A budget of zero-plus-one millisecond cannot be met, so the first check trips.

    The point is not the number: it is that the declared budget is consulted at all, and that
    exceeding it terminates the run rather than being recorded and ignored.
    """
    impatient = capability.model_copy(
        update={"policy": capability.policy.model_copy(update={"max_duration_ms": 1})}
    )
    result = replay.run(impatient, {"member_id": "12345"})

    assert result.status is RunStatus.FAILED
    assert result.error is not None
    assert result.error.code is FailureCode.TIMEOUT
    assert "declared budget" in result.error.message


def test_recovery_is_bounded_across_the_run_not_only_per_rule(
    replay: ReplayExecutor, capability: Capability, app: tuple[str, int]
) -> None:
    """Per-rule budgets bound each rule; this bounds the run.

    Without a run-level cap, five steps at three attempts each is fifteen recovery cycles with every
    individual rule still inside its own budget -- and a configured total of six meaning nothing.
    """
    replay.max_recovery_attempts_total = 2
    arm(app, "transient_load", count=9)
    try:
        result = replay.run(capability, {"member_id": "12345"})
    finally:
        reset(app)

    assert result.recovery_attempts <= 3, "the run-level cap must bite before the per-rule one"
    assert result.status in {RunStatus.FAILED, RunStatus.NEEDS_HUMAN}
    summary = (result.error.message if result.error else "") + (
        result.intervention.reason if result.intervention else ""
    )
    assert "configured total" in summary


# ====================================== an extraction that reads nothing is not success


def test_an_extraction_with_no_readable_value_fails_rather_than_returning_empty(
    replay: ReplayExecutor, capability: Capability
) -> None:
    """The worst kind of failure is the confident one.

    `node.value or node.name or ""` meant a target that resolved but read as nothing produced an
    empty output, the run continued, and it could report SUCCESS. Nothing downstream checks an
    output for emptiness, so it would have travelled as a real answer.
    """
    # "Passbook Ref" is a field the mock core system renders and never populates -- the kind of
    # dead column a real legacy screen accumulates. It resolves cleanly and reads as nothing, which
    # is exactly the case this guard exists for. Retargeting to something that does *not* resolve
    # would test target resolution instead, and pass without the guard.
    blank = Anchor(relation=AnchorRelation.ADJACENT_TO, text="Passbook Ref")
    retargeted = capability.model_copy(
        update={
            "steps": tuple(
                step.model_copy(
                    update={
                        "action": step.action.model_copy(
                            update={
                                "target": step.action.target.model_copy(
                                    update={"name": None, "anchor": blank}
                                )
                            }
                        ),
                        "precondition": None,
                    }
                )
                if step.id == "read_balance"
                else step
                for step in capability.steps
            )
        }
    )
    result = replay.run(retargeted, {"member_id": "12345"})

    assert result.status is not RunStatus.SUCCESS, "an empty extraction must never read as success"
    # Asserted on the code, not merely on "not success". This capability's checkpoint happens to
    # assert the shape of the extracted money value, so it would have caught the empty output a
    # step later anyway -- which is precisely why a weaker assertion here would pass without the
    # guard and prove nothing. A capability whose checkpoint did not pin the value would have
    # returned SUCCESS with an empty answer.
    assert result.error is not None
    assert result.error.code is FailureCode.ACTION_FAILED, result.error.message
    assert "no readable value" in result.error.message
    assert result.error.step_id == "read_balance"
