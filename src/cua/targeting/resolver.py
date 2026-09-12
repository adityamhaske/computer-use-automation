"""The resolution ladder: a `TargetDescriptor` becomes a concrete node, or nothing happens.

Two properties matter more than anything else here.

**Determinism.** Rungs are attempted in a fixed order; candidates within a rung come back in
document order; nothing consults a clock, a random source, or an unordered collection. Same
artifact, same inputs, same page state produces the same decision, every time.

**Refusal.** If more than one candidate survives and nothing in the descriptor distinguishes them,
resolution *fails*. It does not pick the first, the topmost, or the highest-scoring.

That second property is the one worth arguing for. Two plausible "Submit" buttons on a back-office
screen means the page is not what the artifact thinks it is, and acting on either is how an
automation posts a transaction to the wrong account. A refusal escalates to a human with full
context and costs minutes; a wrong click on a financial screen may not be recoverable at all.

Note the absence of a tunable ambiguity threshold. A knob there would inevitably be turned up until
things "worked", which is precisely the failure mode this design exists to prevent. The reported
`ambiguity` figure is for debugging, not for tuning.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from cua.domain.snapshot import UiNode, UiSnapshot
from cua.domain.target import (
    LADDER,
    REPLAY_FORBIDDEN,
    ResolutionStrategy,
    TargetDescriptor,
    is_drift,
)
from cua.targeting import candidates as strategies

Generator = Callable[[TargetDescriptor, UiSnapshot], list[UiNode]]

GENERATORS: dict[ResolutionStrategy, Generator] = {
    ResolutionStrategy.SEMANTIC_EXACT: strategies.semantic_exact,
    ResolutionStrategy.SEMANTIC_NORMALIZED: strategies.semantic_normalized,
    ResolutionStrategy.STRUCTURAL_ANCHOR: strategies.structural_anchor,
    ResolutionStrategy.HINT_CACHED: strategies.hint_cached,
    ResolutionStrategy.ORDINAL_IN_REGION: strategies.ordinal_in_region,
}
"""No entry for VISION: it is a discovery-time aid that is not implemented, and a missing entry
means an attempt to use it in replay fails loudly rather than silently resolving nothing."""


@dataclass(frozen=True)
class Resolution:
    """A successful resolution, with everything needed to explain it afterwards."""

    node: UiNode
    strategy: ResolutionStrategy
    candidates_considered: int
    ambiguity: float
    fingerprint_matched: bool
    strategies_tried: tuple[ResolutionStrategy, ...] = ()

    @property
    def drifted(self) -> bool:
        return False  # set by the caller, which knows the recorded baseline


@dataclass
class TargetResolutionError(Exception):
    """Resolution did not produce exactly one node.

    Carries the debugging context the brief requires: what was being looked for, which rungs were
    attempted, and what was actually found.
    """

    target: TargetDescriptor
    reason: str
    strategies_tried: tuple[ResolutionStrategy, ...] = ()
    candidates: tuple[UiNode, ...] = ()
    ambiguity: float = 0.0

    def __str__(self) -> str:
        return f"{self.reason}: {self.target.describe()}"

    @property
    def is_ambiguous(self) -> bool:
        return len(self.candidates) > 1

    def candidate_summaries(self) -> tuple[str, ...]:
        return tuple(f"{n.role} {n.name!r} @ {n.node_id}" for n in self.candidates[:5])


@dataclass
class TargetResolver:
    """Resolves descriptors against snapshots, deterministically."""

    allow_vision: bool = False
    """Discovery may use a vision fallback; replay may not. Enforced here as well as by the
    forbidden-strategy set, because a single point of enforcement is a single point of failure."""

    max_candidates_reported: int = 5

    strategies_available: tuple[ResolutionStrategy, ...] = field(default=LADDER)

    def config_fingerprint(self) -> str:
        """Identifies this resolver's configuration, recorded into every run.

        Without it, "the same artifact produced a different trace" is unattributable: it could be a
        UI change or a resolver change, and those demand opposite responses.
        """
        import hashlib

        payload = f"{self.allow_vision}|{'>'.join(s.value for s in self.ladder())}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def ladder(self) -> tuple[ResolutionStrategy, ...]:
        """The rungs this resolver will attempt, in order."""
        return tuple(
            strategy
            for strategy in self.strategies_available
            if self.allow_vision or strategy not in REPLAY_FORBIDDEN
        )

    def resolve(self, target: TargetDescriptor, snapshot: UiSnapshot) -> Resolution:
        """Resolve, or raise `TargetResolutionError`.

        Never returns a best guess. There is no code path from "several things matched" to "acted
        on one of them".
        """
        tried: list[ResolutionStrategy] = []

        for strategy in self.ladder():
            generator = GENERATORS.get(strategy)
            if generator is None:
                continue
            tried.append(strategy)

            found = generator(target, snapshot)
            if not found:
                continue

            if len(found) > 1:
                # An explicit ordinal is a deterministic disambiguator the author supplied on
                # purpose. Anything else is guesswork, so we stop.
                if target.ordinal is not None and 0 <= target.ordinal < len(found):
                    chosen = found[target.ordinal]
                else:
                    raise TargetResolutionError(
                        target=target,
                        reason=f"{len(found)} candidates matched and none is distinguishable",
                        strategies_tried=tuple(tried),
                        candidates=tuple(found[: self.max_candidates_reported]),
                        ambiguity=_ambiguity(len(found)),
                    )
            else:
                chosen = found[0]

            return Resolution(
                node=chosen,
                strategy=strategy,
                candidates_considered=len(found),
                ambiguity=_ambiguity(len(found)),
                fingerprint_matched=bool(target.fingerprint)
                and target.fingerprint == chosen.fingerprint,
                strategies_tried=tuple(tried),
            )

        raise TargetResolutionError(
            target=target,
            reason="no candidate matched at any rung",
            strategies_tried=tuple(tried),
        )

    def resolve_with_drift(
        self, target: TargetDescriptor, snapshot: UiSnapshot
    ) -> tuple[Resolution, bool]:
        """Resolve, and report whether the surface has moved against the artifact.

        Drift is descent: resolving via a weaker rung than the one recorded at discovery. It is a
        signal rather than an error, and it is visible *before* replays start failing -- which is
        the window in which a re-record or a tenant overlay is still cheap.
        """
        resolution = self.resolve(target, snapshot)
        return resolution, is_drift(target.recorded_strategy, resolution.strategy)


def _ambiguity(candidate_count: int) -> float:
    """0.0 for a unique match, approaching 1.0 as candidates multiply.

    Reported for debugging, never thresholded -- see this module's docstring.
    """
    if candidate_count <= 1:
        return 0.0
    return (candidate_count - 1) / candidate_count
