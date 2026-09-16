"""The assisted fallback must not cost the determinism guarantee it sits next to.

Stretch goal 4 asks for "a bounded, policy-checked LLM recovery for a single step (never
open-ended)". Every word of that is a constraint, and the dangerous one is the first: an assisted
fallback built inside `cua.replay` would make the central claim of this system -- no model in the
decision loop -- false, while leaving every other test green.

So these assert the boundary rather than the feature:

1. `cua.replay` still cannot reach a model, by source inspection as well as by import contract.
2. The CLI's default path does not even load a model client.
3. Assist asks once. Not once per step, not once per failure -- once.
4. The model chooses from a closed tool set, and never a destination.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cua.agent.llm import LlmResponse, ToolCall
from cua.assist.recovery import ASSISTABLE, AssistedReplay
from cua.domain.capability import Capability
from cua.domain.result import FailureCode, FailureDetail, RunResult, RunStatus
from cua.domain.serde import load_capability
from cua.domain.snapshot import UiNode, UiSnapshot
from cua.evidence.bus import EvidenceBus
from cua.policy.authorized import AuthorizedAction
from cua.policy.config import parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.redact import Redactor
from cua.runtime.dispatcher import Dispatcher
from cua.surfaces.base import ActionResult, SessionInfo
from cua.targeting.resolver import TargetResolver

SRC = Path(__file__).resolve().parents[2] / "src/cua/replay"
BANNED_ROOTS = {"httpx", "openai", "anthropic"}
REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = REPO_ROOT / "config/policy.yaml"
FIXTURE = REPO_ROOT / "tests/fixtures/capabilities/savings_balance.yaml"


@dataclass
class CountingLlm:
    """An `LlmPort` that records how often it was asked anything."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    model_name: str = "counting/stub"

    def complete(
        self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmResponse:
        self.calls.append({"messages": messages, "tools": tools})
        return LlmResponse(text="", tool_calls=(), model=self.model_name, finish_reason="stop")


def test_replay_imports_no_model_client_anywhere() -> None:
    """Parsed from the source, not from the import graph.

    `lint-imports` covers this too, and it was worth learning that a contract can silently stop
    running: `make invariants` invoked import-linter in a form that printed nothing, ran nothing
    and exited zero. A second check that reads the files directly does not share that failure
    mode.
    """
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
                module = ""
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                roots = {module.split(".")[0]}
            else:
                continue

            assert not (roots & BANNED_ROOTS), f"{path.name} imports {roots & BANNED_ROOTS}"
            assert not module.startswith("cua.agent"), f"{path.name} imports {module}"


def test_assist_is_not_reachable_from_replay() -> None:
    """The dependency points one way. `cua.replay` must not know assist exists."""
    for path in SRC.rglob("*.py"):
        assert "cua.assist" not in path.read_text("utf-8"), f"{path.name} references cua.assist"


def test_a_replay_without_assist_never_constructs_a_model_client() -> None:
    """A plain `cua replay` makes zero model calls -- the guarantee, as a test.

    Asserted on the shape of `_replay_once` rather than by importing: the CLI legitimately imports
    a client at module scope for `cua discover`, and an import is not a call. What matters is that
    the only construction of one on the replay path sits inside the branch `--assist` selects, so
    the default path cannot reach a model however the run ends.
    """
    import cua.cli.main as cli

    source = Path(cli.__file__).read_text("utf-8")
    body = source[source.index("def _replay_once") :]
    body = body[: body.index("\ndef ", 1)]

    assert "OpenRouterLlm(" in body, "this test is reading the wrong function"
    before_branch, _, _rest = body.partition("if assist:")
    assert "OpenRouterLlm(" not in before_branch, (
        "a model client is constructed on the default replay path"
    )
    assert "AssistedReplay(" not in before_branch, "assist is wired on the default replay path"
    assert "cua.assist" not in before_branch, "assist is imported on the default replay path"


def test_assist_asks_once() -> None:
    """Bounded by a counter on the run, not by an instruction in the prompt.

    A prompt that says "only try once" is a request. `AssistOutcome.attempted` is a fact, checked
    before the model is reached, so a second failure in the same run cannot open a second round.
    """
    llm = CountingLlm()
    assisted = AssistedReplay(
        executor=None,  # type: ignore[arg-type]
        dispatcher=None,  # type: ignore[arg-type]
        llm=llm,
        evidence=None,  # type: ignore[arg-type]
        redactor=None,  # type: ignore[arg-type]
    )
    assisted.outcome.attempted = True

    class _AlreadyFailed:
        status = None
        error = None

    assert assisted._is_assistable(_AlreadyFailed()) is False
    assert llm.calls == [], "a second failure must not open a second round"


def test_only_recoverable_shapes_of_failure_are_assistable() -> None:
    """Asking a model to get around a refusal is the behaviour a chokepoint exists to prevent."""
    assert FailureCode.TARGET_NOT_FOUND in ASSISTABLE
    assert FailureCode.PRECONDITION_FAILED in ASSISTABLE

    # A policy refusal, a lost lease and bad caller input are not the model's to reinterpret.
    assert FailureCode.INPUT_VALIDATION_FAILED not in ASSISTABLE
    assert FailureCode.NAVIGATION_BLOCKED not in ASSISTABLE
    assert FailureCode.LEASE_LOST not in ASSISTABLE


def test_assist_cannot_choose_navigation() -> None:
    """The closed tool set. A model that may pick a destination can pick an exfiltration target."""
    from cua.domain.target import NameMatch, TargetDescriptor

    target = TargetDescriptor(role="button", name=NameMatch(value="Search"))
    assert AssistedReplay._action_for("navigate", target, {"url": "http://x"}) is None
    assert AssistedReplay._action_for("finish", target, {}) is None
    assert AssistedReplay._action_for("click", target, {}) is not None


# ------------------------------------------------------------------------------------------
# The two bugs below shipped in the first cut of assist: resuming at the corrected step
# instead of after it, and an irreversible-action guard that could never fire. Both need a
# real dispatcher (resolver + policy engine) to reproduce, since the mistake in each case was
# only visible once a real snapshot and a real risk classification were in the loop.


class _FakeDriver:
    """Reports one fixed snapshot and accepts whatever authorized action it is given."""

    def __init__(self, snapshot: UiSnapshot) -> None:
        self._snapshot = snapshot

    def observe(self) -> UiSnapshot:
        return self._snapshot

    def dispatch(self, action: AuthorizedAction) -> ActionResult:
        assert isinstance(action, AuthorizedAction), "the driver must only ever see authorized work"
        return ActionResult(ok=True, duration_ms=1, navigated=False)

    def screenshot(self, *, redact: tuple = ()) -> bytes:
        return b""

    def bounds_for(self, node_ids: tuple[str, ...]) -> dict[str, Any]:
        return {}

    def session_info(self) -> SessionInfo:
        return SessionInfo(session_id="s1", driver="fake", url=self._snapshot.url)

    def close(self) -> None:
        return None


class _OneShotLlm:
    """Proposes one fixed tool call, whatever it is asked."""

    model_name = "fixed/one-shot"

    def __init__(self, node_id: str, tool: str = "click") -> None:
        self._node_id = node_id
        self._tool = tool

    def complete(
        self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmResponse:
        return LlmResponse(
            tool_calls=(ToolCall(id="1", name=self._tool, arguments={"node_id": self._node_id}),),
            model=self.model_name,
            finish_reason="tool_calls",
        )


class _FakeExecutor:
    """`run` returns a fixed failure; `resume` records its arguments rather than replaying."""

    def __init__(self, failed: RunResult, resumed: RunResult) -> None:
        self._failed = failed
        self._resumed = resumed
        self.resume_calls: list[dict[str, Any]] = []

    def run(self, capability: Capability, supplied: dict[str, str]) -> RunResult:
        return self._failed

    def resume(
        self,
        capability: Capability,
        supplied: dict[str, str],
        *,
        from_index: int,
        prior_outputs: dict[str, str] | None = None,
        prior_recovery_attempts: int = 0,
    ) -> RunResult:
        self.resume_calls.append({"from_index": from_index})
        return self._resumed


def _capability() -> Capability:
    return load_capability(FIXTURE.read_text(encoding="utf-8"))


def _rig(tmp_path: Path, snapshot: UiSnapshot) -> tuple[Dispatcher, EvidenceBus, Redactor]:
    config = parse_policy(POLICY.read_text(encoding="utf-8"))
    redactor = Redactor(config.redaction)
    bus = EvidenceBus(tmp_path, redactor, run_id="assist-test")
    dispatcher = Dispatcher(
        driver=_FakeDriver(snapshot),
        policy=PolicyEngine(config),
        resolver=TargetResolver(),
        evidence=bus,
    )
    return dispatcher, bus, redactor


def test_assist_resumes_after_the_corrected_step_not_at_it(tmp_path: Path) -> None:
    """Regression: `from_index=step_index` re-dispatched the step the correction just performed.

    `reconcile` only skips a step whose postcondition already holds, and a compiled capability
    declares none, so resuming at the failed step's own index found no evidence it was done and
    re-ran it -- a duplicate click on top of the one assist had just made. The fix resumes one
    step later, where the corrected step's role has already been discharged.
    """
    capability = _capability()
    failed_index = [s.id for s in capability.steps].index("submit_search")

    snapshot = UiSnapshot(
        snapshot_id="s1",
        url="http://localhost:8811/search",
        nodes=(UiNode(node_id="n1", role="button", name="Search"),),
    )
    dispatcher, bus, redactor = _rig(tmp_path, snapshot)

    failed = RunResult(
        capability_id=capability.id,
        capability_version=capability.version,
        run_id="run-1",
        status=RunStatus.FAILED,
        error=FailureDetail(
            code=FailureCode.TARGET_NOT_FOUND, message="not found", step_id="submit_search"
        ),
    )
    resumed = RunResult(
        capability_id=capability.id,
        capability_version=capability.version,
        run_id="run-1",
        status=RunStatus.SUCCESS,
    )
    executor = _FakeExecutor(failed, resumed)

    assisted = AssistedReplay(
        executor=executor,  # type: ignore[arg-type]
        dispatcher=dispatcher,
        llm=_OneShotLlm("n1"),  # type: ignore[arg-type]
        evidence=bus,
        redactor=redactor,
    )

    result = assisted.run(capability, {"member_id": "12345"})

    assert executor.resume_calls == [{"from_index": failed_index + 1}]
    assert result is resumed


def test_assist_refuses_an_irreversible_correction(tmp_path: Path) -> None:
    """Regression: the guard tested `action.risk`, which `_action_for` never sets.

    `Click`/`Type`/`Select` built fresh here carry no risk annotation, so the old check
    (`action.risk is ActionRisk.IRREVERSIBLE`) could not fire regardless of what the model
    picked. The fix asks the same classifier the policy chokepoint uses, which also weighs the
    target's name -- so a model proposing a click on a control the lexicon marks irreversible
    ("Transfer Funds") is refused here, before a dispatch is ever attempted.
    """
    capability = _capability()
    snapshot = UiSnapshot(
        snapshot_id="s1",
        url="http://localhost:8811/search",
        nodes=(UiNode(node_id="n1", role="button", name="Transfer Funds"),),
    )
    dispatcher, bus, redactor = _rig(tmp_path, snapshot)

    result = RunResult(
        capability_id=capability.id,
        capability_version=capability.version,
        run_id="run-1",
        status=RunStatus.FAILED,
        error=FailureDetail(
            code=FailureCode.TARGET_NOT_FOUND, message="not found", step_id="submit_search"
        ),
    )

    assisted = AssistedReplay(
        executor=None,  # type: ignore[arg-type]
        dispatcher=dispatcher,
        llm=_OneShotLlm("n1"),  # type: ignore[arg-type]
        evidence=bus,
        redactor=redactor,
    )

    action = assisted._ask(capability, result)

    assert action is None
    assert "irreversible" in assisted.outcome.reason
