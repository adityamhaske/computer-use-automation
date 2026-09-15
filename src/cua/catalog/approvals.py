"""Where a capability's approval state lives.

`CapabilityApproval` has existed since ADR 0002 and `ReplayExecutor` has always gated on it, but
nothing persisted it, set it, or showed it -- so the gate was real and unreachable, which is the
same as not having one. A reviewer could not approve a capability, could not see that one was
unapproved, and could not watch the gate refuse. This module is the missing half.

A JSON file beside the artifact, for the same reason the artifact itself is a file: it is a
governance record that belongs in version control next to the thing it governs, and a diff of it
should be readable. `.json` rather than `.yaml` so it can never be mistaken for a capability by
`CapabilityStore.list()`, which globs `*.yaml`.

Kept out of the artifact on purpose (ADR 0002): approval changes as people review, the version
does not change with it, and a definition that mutates on a decision altering none of its
behaviour stops being reviewable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cua.domain.approval import ApprovalState, CapabilityApproval
from cua.domain.evaluation import CapabilityEvaluation

DEFAULT_ROOT = Path("evidence/capabilities")
DEFAULT_EVALS = Path("evidence/evals")


@dataclass
class ApprovalStore:
    """Approval records on disk, addressed by the same `id@version` the catalog uses."""

    root: Path = DEFAULT_ROOT
    evals_root: Path = DEFAULT_EVALS

    def path_for(self, ref: str) -> Path:
        return self.root / f"{ref}.approval.json"

    def load(self, ref: str) -> CapabilityApproval | None:
        """The stored decision, or None if nobody has recorded one.

        None is not the same as `DRAFT`: it means no reviewer has looked at this capability at
        all. `state_for` collapses the two for display, because to a caller the effect is
        identical -- neither permits unattended replay -- but the distinction matters when
        deciding whether there is a record to revoke.
        """
        path = self.path_for(ref)
        if not path.exists():
            return None
        return CapabilityApproval.model_validate_json(path.read_text("utf-8"))

    def state_for(self, ref: str, *, content_hash: str) -> ApprovalState:
        """What the catalog should display for this exact content.

        An approval that does not pin this content hash reads as `DRAFT` rather than as approved:
        the artifact has been edited since somebody signed it off, and inheriting that signature
        is precisely what the hash pinning exists to prevent.
        """
        approval = self.load(ref)
        if approval is None:
            return ApprovalState.DRAFT
        if approval.state is ApprovalState.APPROVED and approval.content_hash != content_hash:
            return ApprovalState.DRAFT
        return approval.state

    def record(
        self,
        *,
        ref: str,
        content_hash: str,
        state: ApprovalState,
        by: str,
        notes: str = "",
    ) -> CapabilityApproval:
        """Write a decision. The timestamp is set here so a caller cannot backdate one."""
        approval = CapabilityApproval(
            capability_ref=ref,
            content_hash=content_hash,
            state=state,
            approved_by=by,
            approved_at=datetime.now(UTC),
            notes=notes,
        )
        path = self.path_for(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(approval.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return approval

    def evaluation_for(
        self, ref: str, *, content_hash: str | None = None
    ) -> CapabilityEvaluation | None:
        """The measured evidence for this capability, if `cua eval` has been run.

        Matched on `capability_ref` rather than on filename, because the suites are named for what
        they measure (`replay_stability`, `cross_tenant`) and not for what they measured it on.
        Where several suites cover the same capability the one with the most runs wins -- a
        recommendation should rest on the largest sample available, not on whichever file sorted
        first.

        `content_hash` narrows to evidence measured against *this exact artifact*, and callers
        making an approval decision must pass it. `CapabilityEvaluation` carries the hash it was
        measured against precisely so this can be checked, and without the check "approved on the
        evidence" can mean approved on evidence gathered from a different version -- which is the
        same defect as inheriting an approval across an edit, arriving by a different route.
        """
        if not self.evals_root.exists():
            return None

        best: CapabilityEvaluation | None = None
        for path in sorted(self.evals_root.glob("*.evaluation.json")):
            try:
                candidate = CapabilityEvaluation.model_validate_json(path.read_text("utf-8"))
            except Exception:
                # A malformed evaluation must not stop an approval decision from being made. It
                # is evidence, not a dependency.
                continue
            if candidate.capability_ref != ref:
                continue
            if content_hash is not None and candidate.content_hash != content_hash:
                continue
            if best is None or candidate.runs > best.runs:
                best = candidate
        return best
