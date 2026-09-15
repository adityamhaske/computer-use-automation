"""Governance state about a frozen capability version.

Separate from the capability itself (ADR 0002). Approval is *about* a version -- it changes as
people review, and the version does not change with it. Keeping it inside the artifact would mean
the definition mutates on a decision that alters none of its behaviour.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from cua.domain.evaluation import CapabilityEvaluation


class ApprovalState(StrEnum):
    DRAFT = "draft"
    """Freshly compiled from a discovery run. The compiler's parameter lifting and outcome
    scaffolding are heuristic, so its output is a proposal, not a fact."""

    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"
    """Was approved; withdrawn after a problem was found. Distinct from REJECTED so the history
    stays legible."""


class CapabilityApproval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    capability_ref: str
    content_hash: str
    """Pins approval to exact content. Editing an artifact after approval invalidates it rather than
    silently inheriting it. Otherwise "approved" would quietly mean "some earlier version was
    approved"."""

    state: ApprovalState = ApprovalState.DRAFT
    approved_by: str | None = None
    approved_at: datetime | None = None
    notes: str = ""

    def permits_unattended_replay(self, *, content_hash: str) -> bool:
        """Whether an irreversible step may run without a human present.

        Requires approval of *this exact content*. Combined with an explicit caller opt-in, this is
        the gate on money-moving automation.

        Both hashes must be non-empty. Equality alone was not enough: an unsealed artifact has
        `content_hash == ""`, and a hand-written approval record carrying `""` compared equal to
        it -- so a file anyone could author approved a capability nobody had sealed, let alone
        reviewed. "Approved at this exact content" has no meaning when there is no content hash to
        pin, and the honest answer there is no, not yes.
        """
        if not self.content_hash or not content_hash:
            return False
        return self.state is ApprovalState.APPROVED and self.content_hash == content_hash

    @staticmethod
    def recommend(evaluation: CapabilityEvaluation) -> tuple[bool, str]:
        """What the measured evidence suggests, and why. A proposal, never a decision.

        Deliberately not a method that returns an approved `CapabilityApproval`. Approval is a
        person taking responsibility for a capability running unattended against a banking system;
        a function that could hand that out would make the gate decorative. So this returns a
        recommendation and the sentence behind it, and `cua catalog approve` still requires
        somebody to type it.

        The threshold is `CapabilityEvaluation.is_trustworthy`, which is strict on purpose: any
        wrong action at all disqualifies, however good the success rate looks. One wrong click on
        a financial screen is not averaged away.
        """
        if evaluation.runs == 0:
            return False, "no measured runs -- run `cua eval` first"
        if evaluation.wrong_actions:
            return (
                False,
                f"{evaluation.wrong_actions} wrong action(s) recorded; "
                "any wrong action disqualifies regardless of success rate",
            )
        if evaluation.runs < 5:
            return False, f"only {evaluation.runs} run(s) measured; at least 5 are needed"
        if evaluation.stability_score < 0.95:
            return (
                False,
                f"stability {evaluation.stability_score:.0%} is below the 95% threshold",
            )
        return (
            True,
            f"{evaluation.runs} runs, {evaluation.stability_score:.0%} stable, "
            f"zero wrong actions, mean drift {evaluation.mean_drift:.2f}",
        )
