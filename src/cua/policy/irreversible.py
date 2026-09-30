"""The gate an irreversible automation step must clear.

One check, called from two places on purpose. `ReplayExecutor` calls it before the browser moves, so
an unapproved capability is refused at no cost. `PolicyEngine.authorize` calls it again at the
dispatch of the irreversible step itself, so the chokepoint never takes the executor's word for it.
A gate that the chokepoint accepted on faith would be a gate with a bypass.

For a long time only the first half existed, and the dispositions table said `block_and_escalate`
for every irreversible automation step -- so a capability that cleared both replay gates was still
refused at dispatch, and no approval, however valid, could ever permit one. The gate was enforced
and structurally unsatisfiable, which is the same as not having one. `allow_if_approved` is the
disposition that consults this; `block_and_escalate` still never does.
"""

from __future__ import annotations

from dataclasses import dataclass

from cua.domain.approval import CapabilityApproval
from cua.domain.capability import Capability
from cua.domain.tenant_binding import AppliedBinding
from cua.policy.config import ReplayGates


@dataclass(frozen=True)
class IrreversibleGrant:
    """What a run presents to be allowed an irreversible step. Verified at every use, never trusted.

    Scoped to one run by construction: the approval is pinned to exact content, the opt-in belongs
    to the invocation, and the binding names the one base and tenant context this run derived its
    capability from. Nothing here is persisted or reusable across runs.
    """

    approval: CapabilityApproval | None = None
    caller_opt_in: bool = False
    applied_binding: AppliedBinding | None = None
    """How the executing capability was derived from the sealed base the approval pins, when a
    tenant binding produced it. Without it, the approval must pin the executing content itself."""

    def refusal(self, capability: Capability, gates: ReplayGates) -> str | None:
        """Why `capability` may not perform an irreversible step unattended, or None if it may."""
        if gates.irreversible_requires_caller_optin and not self.caller_opt_in:
            return (
                "this capability performs an irreversible action and the caller did not opt in "
                "(allow_irreversible)"
            )
        if gates.irreversible_requires_approval:
            return self._approval_refusal(capability)
        return None

    def basis(self, capability: Capability) -> str:
        """What the evidence records about the grant an allowed irreversible step ran under."""
        opt_in = f"caller opt-in: {'yes' if self.caller_opt_in else 'no'}"
        if self.approval is None:
            return f"no approval required by replay_gates; {opt_in}"
        via = (
            f", via tenant binding {self.applied_binding.binding.tenant!r}"
            if self.applied_binding is not None
            and self.approval.content_hash != capability.content_hash
            else ""
        )
        return (
            f"{self.approval.capability_ref} approved by {self.approval.approved_by or 'unknown'} "
            f"at {self.approval.content_hash}{via}; {opt_in}"
        )

    def _approval_refusal(self, capability: Capability) -> str | None:
        refused = (
            f"this capability performs an irreversible action and {capability.ref} is not "
            "approved at this exact content hash"
        )
        if self.approval is None:
            return refused

        # Against the *verified* hash, not the declared one. `content_hash` is a line in a file the
        # editor also controls: an edited artifact keeps its old declared hash, so comparing against
        # it let a stale approval match and a tampered capability run unattended.
        if capability.hash_is_valid() and self.approval.permits_unattended_replay(
            content_hash=capability.content_hash
        ):
            return None

        # Otherwise the approval may only reach this capability *through* the binding that produced
        # it -- re-derived here rather than believed, so a capability altered after binding does not
        # inherit its base's approval.
        if self.applied_binding is None:
            return refused
        if (problem := self.applied_binding.verify(capability)) is not None:
            return f"{refused}: {problem}"
        if not self.applied_binding.binding.substitutes_only:
            return (
                f"{refused}: the binding for tenant {self.applied_binding.binding.tenant!r} "
                "overrides steps, recovery or the checkpoint, which an approval of the base does "
                "not cover"
            )
        if not self.approval.permits_unattended_replay(
            content_hash=self.applied_binding.base.content_hash
        ):
            return refused
        return None
