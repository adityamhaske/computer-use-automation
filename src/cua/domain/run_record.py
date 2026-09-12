"""The record of one execution.

Append-only, redacted, actor-tagged. This is what makes a run debuggable without reproducing it,
which the brief requires -- and what makes the policy chokepoint *verifiable* rather than merely
claimed: every dispatch here must have a matching authorization, and a test asserts it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cua.domain.actor import Actor
from cua.domain.result import ObservationClass, RunResult
from cua.domain.target import ResolutionStrategy


class RunKind(StrEnum):
    DISCOVERY = "discovery"
    """LLM in the loop, probabilistic."""
    REPLAY = "replay"
    """No model, deterministic."""
    INTERVENTION = "intervention"
    """A human operating the live session during an escalation."""


class ResolutionRecord(BaseModel):
    """How one target was resolved. The raw material for drift detection and debugging."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_description: str
    strategy_recorded: ResolutionStrategy | None = None
    strategy_used: ResolutionStrategy | None = None
    resolved_node_id: str | None = None
    candidates_considered: int = 0
    ambiguity_score: float | None = None
    fingerprint_matched: bool | None = None

    @property
    def drifted(self) -> bool:
        from cua.domain.target import is_drift

        if self.strategy_recorded is None or self.strategy_used is None:
            return False
        return is_drift(self.strategy_recorded, self.strategy_used)


class StepRecord(BaseModel):
    """One step, as executed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int
    step_id: str
    action_type: str
    actor: Actor = Actor.AUTOMATION
    lease_epoch: int = 0
    """Together with `actor`, this is what makes a human takeover auditable: every action says who
    did it and under which grant of control."""

    authorized: bool = False
    """Whether PolicyEngine minted an AuthorizedAction for this. A dispatch without one is an
    invariant violation, not a warning."""
    policy_decision: str | None = None

    observation: ObservationClass | None = None
    resolution: ResolutionRecord | None = None

    started_at: datetime | None = None
    duration_ms: int = 0
    recovery_rule_applied: str | None = None
    error_code: str | None = None
    snapshot_ref: str | None = None
    screenshot_ref: str | None = None


class RunRecord(BaseModel):
    """The full record of one run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    kind: RunKind
    capability_ref: str | None = None
    capability_content_hash: str | None = None
    """Names exactly what executed. With immutable capabilities this is unambiguous -- which is the
    whole reason the definition is not allowed to mutate."""

    tenant: str | None = None
    goal: str | None = None
    """Discovery only: the natural-language goal."""

    inputs: dict[str, Any] = Field(default_factory=dict)
    """Already redacted. Sensitive inputs appear as shapes, never values."""

    steps: tuple[StepRecord, ...] = ()
    result: RunResult | None = None

    started_at: datetime | None = None
    duration_ms: int = 0
    evidence_ref: str | None = None

    model_name: str | None = None
    tokens_used: int | None = None
    """Discovery only. Always None on a replay -- a non-None value there would itself be a bug."""

    resolver_config_hash: str | None = None
    """Freezes the resolver config into the record, so a trace can be reproduced exactly."""

    human_actions: int = 0
    lease_transitions: tuple[str, ...] = ()

    @property
    def drift_score(self) -> float:
        """Share of resolutions that fell to a weaker rung than recorded.

        0.0 means the surface matched the artifact exactly. Rising values are the early warning.
        """
        resolutions = [s.resolution for s in self.steps if s.resolution is not None]
        if not resolutions:
            return 0.0
        return sum(1 for r in resolutions if r.drifted) / len(resolutions)

    @property
    def strategy_mix(self) -> dict[str, int]:
        mix: dict[str, int] = {}
        for step in self.steps:
            if step.resolution and step.resolution.strategy_used:
                key = step.resolution.strategy_used.value
                mix[key] = mix.get(key, 0) + 1
        return mix

    @property
    def unauthorized_dispatches(self) -> tuple[StepRecord, ...]:
        """Steps that reached a surface without authorization.

        Must always be empty. This property exists so the claim is checkable from the evidence
        rather than taken on trust.
        """
        return tuple(s for s in self.steps if not s.authorized)
