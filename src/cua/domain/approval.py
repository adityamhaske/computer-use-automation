"""Governance state about a frozen capability version.

Separate from the capability itself (ADR 0002). Approval is *about* a version -- it changes as
people review, and the version does not change with it. Keeping it inside the artifact would mean
the definition mutates on a decision that alters none of its behaviour.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


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
        """
        return self.state is ApprovalState.APPROVED and self.content_hash == content_hash
