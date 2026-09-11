"""Aggregate quality signal for a capability version, derived from many runs.

Separate from the capability (ADR 0002): this is derived data. It can be recomputed from run
records, or thrown away, without touching the definition.

The headline metric is `wrong_actions`, and its target is zero. A system that refuses is acceptable
-- it escalates with full context and a human resolves it. A system that clicks the wrong control in
a core banking screen is an incident. Refusal rate and wrong-action rate are therefore reported
separately and never traded off against each other.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CapabilityEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    capability_ref: str
    content_hash: str
    tenant: str | None = None

    runs: int = 0
    successes: int = 0
    business_outcomes: int = 0
    needs_human: int = 0
    failures: int = 0

    wrong_actions: int = 0
    """Acted on a control that was not the intended one. Target: zero, always."""

    ambiguity_refusals: int = 0
    """Declined to act because two candidates matched. A cost, not a defect -- the alternative is a
    coin flip on which button to press."""

    determinism_holds: bool | None = None
    """Whether repeated replays produced identical decision traces. None if not measured."""

    mean_drift: float = 0.0
    strategy_mix: dict[str, int] = Field(default_factory=dict)
    """How often each ladder rung won. A shift toward weaker rungs is an early warning that the
    surface is moving, visible well before replays start failing."""

    fallback_rate: float = 0.0
    """Share of resolutions that fell below their recorded rung."""

    @property
    def stability_score(self) -> float:
        """Share of runs that produced a usable answer.

        Business outcomes count as stable: "no such member" is the capability working correctly.
        Counting them as instability would punish a capability for being asked about a member who
        does not exist.
        """
        if self.runs == 0:
            return 0.0
        return (self.successes + self.business_outcomes) / self.runs

    @property
    def is_trustworthy(self) -> bool:
        """Whether this is fit for unattended replay, on the evidence so far.

        Deliberately strict: any wrong action at all disqualifies, regardless of how good the
        success rate looks. One wrong click on a financial screen is not averaged away.
        """
        return (
            self.runs >= 5
            and self.wrong_actions == 0
            and self.stability_score >= 0.95
            and self.determinism_holds is not False
        )
