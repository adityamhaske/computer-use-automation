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

from cua.agent.llm import LlmResponse
from cua.assist.recovery import ASSISTABLE, AssistedReplay
from cua.domain.result import FailureCode

SRC = Path(__file__).resolve().parents[2] / "src/cua/replay"
BANNED_ROOTS = {"httpx", "openai", "anthropic"}


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
