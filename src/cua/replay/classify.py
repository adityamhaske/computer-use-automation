"""Deciding what a screen means.

The five-step procedure that separates a legitimate answer from a transient hiccup from a defect
from a screen this capability simply does not understand. The brief names conflating the first and
third as the most common design mistake in this problem, and getting the *order* wrong is how it
happens: a generic failure check that runs before the declared outcomes will classify "no records
found" as a broken page every time.

### A refinement to the documented rule

The design said a state "matching nothing the capability declares" is `UNEXPECTED_STATE`. Taken
literally that is unworkable: a freshly compiled capability declares no outcomes at all, so every
page would be unrecognized and every replay would escalate on its first step.

The rule that actually holds is narrower, and is the one the brief asks for: fail closed when a
*declared expectation* is violated and nothing declared explains why. A step with no precondition
asserts nothing, so there is nothing to violate -- it proceeds. A step whose precondition fails has
had an explicit expectation broken, and if no outcome or recovery rule accounts for it, the
executor stops rather than guessing what the screen is.

That keeps the guarantee where it matters -- an unexplained deviation never becomes "continue and
see" -- without demanding that a capability enumerate every page in the application before it can
run at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cua.domain.capability import Capability, Outcome, RecoveryRule, Step
from cua.domain.result import ObservationClass
from cua.domain.snapshot import UiSnapshot


@dataclass(frozen=True)
class Classification:
    observation: ObservationClass
    outcome: Outcome | None = None
    recovery: RecoveryRule | None = None
    reason: str = ""

    @property
    def proceed(self) -> bool:
        return self.observation is ObservationClass.EXPECTED


def classify(
    snapshot: UiSnapshot,
    *,
    capability: Capability,
    inputs: Mapping[str, Any],
    step: Step | None = None,
    checking_checkpoint: bool = False,
) -> Classification:
    """What does this screen mean, given what the capability declares?

    Order is load-bearing and is asserted by `tests/integration/test_fault_matrix.py`.
    """
    # 1. A declared business outcome is an ANSWER. It is checked first, before any failure
    #    reasoning, because "no records found" is a legitimate result and a generic failure check
    #    running first would misread it as a broken page every single time.
    for outcome in capability.outcomes:
        if outcome.detect.evaluate(snapshot, inputs):
            return Classification(
                ObservationClass.BUSINESS_OUTCOME,
                outcome=outcome,
                reason=f"declared outcome {outcome.code!r}",
            )

    # 2. A declared, bounded transient condition.
    for rule in capability.recovery:
        if rule.detect.evaluate(snapshot, inputs):
            return Classification(
                ObservationClass.RECOVERABLE,
                recovery=rule,
                reason=f"recovery rule {rule.id!r}",
            )

    # 3. The declared expectation for this point in the flow.
    if checking_checkpoint:
        if capability.checkpoint.evaluate(snapshot, inputs):
            return Classification(ObservationClass.EXPECTED, reason="checkpoint satisfied")
        return Classification(
            ObservationClass.UNEXPECTED_STATE,
            reason=(
                "the flow completed but the checkpoint does not hold, and no declared outcome or "
                f"recovery rule explains it: {capability.checkpoint.describe()}"
            ),
        )

    if step is not None and step.precondition is not None:
        if step.precondition.evaluate(snapshot, inputs):
            return Classification(ObservationClass.EXPECTED, reason="precondition satisfied")
        return Classification(
            ObservationClass.UNEXPECTED_STATE,
            reason=(
                f"step {step.id!r} expected {step.precondition.describe()}, which does not hold, "
                "and no declared outcome or recovery rule explains it"
            ),
        )

    # 4. Nothing was asserted about this point, so nothing has been violated.
    return Classification(ObservationClass.EXPECTED, reason="no precondition declared")
