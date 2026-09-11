"""When to stop a discovery run.

Every one of these is a bound on a *probabilistic* process, which is the half of the system where
things can run away. The replay engine needs none of this: it executes a fixed list of steps and is
finished.

Budgets are hard rather than advisory. A loop that politely suggests a model wind down is a loop
that spends the budget anyway.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


class StopReason(StrEnum):
    GOAL_MET = "goal_met"
    GAVE_UP = "gave_up"
    """The model declined to guess. A legitimate ending, and a better one than a wrong action."""
    MAX_STEPS = "max_steps"
    TIMEOUT = "timeout"
    TOKEN_BUDGET = "token_budget"
    DEAD_END = "dead_end"
    """Several actions in a row changed nothing. Usually a control the model keeps clicking that
    does not do what it thinks, and the loop cannot tell the difference from the inside."""
    POLICY_WALL = "policy_wall"
    """Repeatedly denied. The goal needs authority this run does not have; continuing would just
    generate more refusals."""
    ERROR = "error"


@dataclass
class Budget:
    """The limits a discovery run may not exceed."""

    max_steps: int = 40
    max_duration_s: float = 300.0
    max_tokens: int = 200_000
    max_unchanged_steps: int = 3
    max_consecutive_denials: int = 3

    started_at: float = field(default_factory=time.monotonic)
    steps: int = 0
    tokens: int = 0
    unchanged_steps: int = 0
    consecutive_denials: int = 0

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    def record_step(self, *, tokens: int, page_changed: bool, denied: bool) -> None:
        self.steps += 1
        self.tokens += tokens
        self.unchanged_steps = 0 if page_changed else self.unchanged_steps + 1
        self.consecutive_denials = self.consecutive_denials + 1 if denied else 0

    def exceeded(self) -> StopReason | None:
        """The first limit that has been hit, if any."""
        if self.steps >= self.max_steps:
            return StopReason.MAX_STEPS
        if self.elapsed_s >= self.max_duration_s:
            return StopReason.TIMEOUT
        if self.tokens >= self.max_tokens:
            return StopReason.TOKEN_BUDGET
        if self.unchanged_steps >= self.max_unchanged_steps:
            return StopReason.DEAD_END
        if self.consecutive_denials >= self.max_consecutive_denials:
            return StopReason.POLICY_WALL
        return None

    def summary(self) -> dict[str, float | int]:
        return {
            "steps": self.steps,
            "tokens": self.tokens,
            "elapsed_s": round(self.elapsed_s, 2),
        }
