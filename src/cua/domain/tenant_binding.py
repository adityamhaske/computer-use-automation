"""Per-tenant specialization of a shared capability.

The economics of this system live here. If automating a flow for one credit union meant re-recording
it for the next two hundred, the product would not work. So capabilities are namespaced by *vendor
product*, and everything a particular institution does differently is expressed as an overlay --
never as a fork (ADR 0005).

Two kinds of divergence, needing two different answers:

**Markup churn.** A tenant restyles the page: different CSS classes, different form field names,
same labels. No overlay needed -- the resolution ladder absorbs this, because it never depended on
those things in the first place.

**Rebranding.** A tenant relabels a control: "Savings Balance" becomes "Savings Bal.". This defeats
name matching *and* structural anchoring, because the anchor text is the label. There is
deliberately
no automatic recovery: inferring that the two mean the same thing is a guess, and a system that
guesses which row holds a balance will eventually read the wrong one. A four-line overlay names the
new label instead.

Resolution is `base ⊕ overlay`, producing an effective capability that is still immutable and whose
content hash covers the overlay -- so a run record names exactly what executed, tenant included.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cua.domain.capability import Capability, RecoveryRule, WaitPolicy
from cua.domain.snapshot import NodeScope
from cua.domain.target import Anchor, NameMatch


class TargetOverride(BaseModel):
    """Fields to replace on a step's target. Anything omitted is inherited."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str | None = None
    name: NameMatch | None = None
    scope: NodeScope | None = None
    anchor: Anchor | None = None
    ordinal: int | None = None
    hints: dict[str, str] | None = None


class StepOverride(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target: TargetOverride | None = None
    wait: WaitPolicy | None = None


class TenantBinding(BaseModel):
    """One institution's deployment of a shared capability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "1.0.0"
    capability_ref: str
    """`id@version`. Pinned: a base version bump must be reviewed against this overlay rather than
    silently inherited, because an override can refer to a step that no longer exists."""

    tenant: str
    description: str = ""

    vars: dict[str, str] = Field(default_factory=dict)
    """Substituted into `{placeholder}` slots -- typically `base_url`."""

    overrides: dict[str, StepOverride] = Field(default_factory=dict)
    recovery_extra: tuple[RecoveryRule, ...] = ()
    """Tenant-specific interstitials. Some institutions add their own banners and notices."""

    def apply(self, capability: Capability) -> Capability:
        """Produce the effective capability for this tenant.

        Fails closed on an override naming a step that does not exist. That is almost always a base
        version bump that removed or renamed the step, and silently ignoring it would leave the
        tenant running unpatched behaviour while the overlay looked applied.
        """
        expected_ref = capability.ref
        if self.capability_ref != expected_ref:
            raise ValueError(
                f"binding targets {self.capability_ref!r} but was applied to {expected_ref!r}"
            )

        unknown = set(self.overrides) - {s.id for s in capability.steps}
        if unknown:
            raise ValueError(
                f"tenant {self.tenant!r} overrides unknown step(s) {sorted(unknown)} of "
                f"{expected_ref} -- the base capability may have changed"
            )

        payload: dict[str, Any] = capability.model_dump(by_alias=True, mode="json")

        for step in payload["steps"]:
            override = self.overrides.get(step["id"])
            if override is None:
                continue
            if override.target is not None:
                target = step.get("action", {}).get("target")
                if target is None:
                    raise ValueError(
                        f"tenant {self.tenant!r} overrides the target of step {step['id']!r}, "
                        "which has no target"
                    )
                patch = override.target.model_dump(by_alias=True, mode="json", exclude_none=True)
                target.update(patch)
            if override.wait is not None:
                step["wait"] = override.wait.model_dump(by_alias=True, mode="json")

        if self.recovery_extra:
            payload["recovery"] = list(payload.get("recovery", [])) + [
                rule.model_dump(by_alias=True, mode="json") for rule in self.recovery_extra
            ]

        payload = _substitute(payload, self.vars)
        payload["content_hash"] = ""
        return Capability.model_validate(payload).with_hash()


def _substitute(node: Any, variables: dict[str, str]) -> Any:
    """Replace `{name}` placeholders throughout, leaving unknown ones intact.

    Unknown placeholders are left alone rather than raising: `{$input: ...}` style references and
    regex braces also live in these documents, and eagerly erroring on them would make the overlay
    mechanism fight the schema.
    """
    if isinstance(node, dict):
        return {k: _substitute(v, variables) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, variables) for v in node]
    if isinstance(node, str):
        for key, value in variables.items():
            node = node.replace("{" + key + "}", value)
        return node
    return node
