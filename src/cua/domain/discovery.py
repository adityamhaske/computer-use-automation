"""The record of a discovery run: what the model tried, and what came of it.

A domain type rather than an agent internal, for the same reason `RunRecord` is: it is the *output*
of the probabilistic half and the *input* to the compiler, so it belongs to neither. Putting it here
keeps `cua.recorder` free of any dependency on `cua.agent`, which matters more than it looks --
the no-LLM-in-replay invariant is about transitive reach, and a trace type living in the agent
package would drag the model client along behind every module that needed to read one.

Pure data. The loop that produces it and the budgets that bound it live in `cua.agent`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from cua.domain.target import ResolutionStrategy, TargetDescriptor

TERMINAL_TOOLS: frozenset[str] = frozenset({"finish", "give_up"})


class StopReason(StrEnum):
    """Why a discovery run ended.

    Every one of these bounds a probabilistic process. Replay needs none of them: it executes a
    fixed list of steps and is finished.
    """

    GOAL_MET = "goal_met"
    GAVE_UP = "gave_up"
    """The model declined to guess. A legitimate ending, and a better one than a wrong action."""
    MAX_STEPS = "max_steps"
    TIMEOUT = "timeout"
    TOKEN_BUDGET = "token_budget"
    DEAD_END = "dead_end"
    """Several actions in a row changed nothing -- usually a control the model keeps clicking that
    does not do what it thinks, which the loop cannot diagnose from the inside."""
    POLICY_WALL = "policy_wall"
    """Repeatedly denied. The goal needs authority this run does not have."""
    ERROR = "error"


@dataclass
class DiscoveryStep:
    """One model decision and its result. The raw material the compiler works from."""

    index: int
    tool: str
    why: str
    """The model's own stated reason. Brief §3.5 asks for what the agent did *and why*, and taking
    it as a required tool argument means every step has one without a second call to ask."""
    arguments: dict[str, Any]
    node_id: str | None = None
    descriptor: TargetDescriptor | None = None
    strategy: ResolutionStrategy | None = None
    descriptor_verified: bool = False
    """Whether the synthesized description resolved back to the node the model picked.

    Checked during the run, while a model is still in the loop, rather than surfacing as a
    capability that fails on its first replay weeks later.
    """
    ok: bool = False
    detail: str = ""
    url_before: str = ""
    extracted: tuple[str, str] | None = None


@dataclass
class DiscoveryRun:
    run_id: str
    goal: str
    target_url: str
    stop_reason: StopReason
    steps: list[DiscoveryStep] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    summary: str = ""
    checkpoint_hint: str = ""
    """The model's own statement of what proves the goal was reached.

    A hint, not a checkpoint. The compiler builds a machine-checkable assertion from the observed
    outputs instead: trusting a model's self-report as a success condition would make the checkpoint
    exactly as reliable as the run it came from.
    """
    budget: dict[str, float | int] = field(default_factory=dict)
    model: str = ""

    @property
    def succeeded(self) -> bool:
        return self.stop_reason is StopReason.GOAL_MET

    @property
    def effective_steps(self) -> list[DiscoveryStep]:
        """Steps that actually did something -- the only ones worth compiling.

        A discovery run wanders: refused actions, a click that did nothing, a field typed twice.
        Compiling the wandering would produce a capability that faithfully reproduces the model's
        confusion.
        """
        return [step for step in self.steps if step.ok and step.tool not in TERMINAL_TOOLS]
