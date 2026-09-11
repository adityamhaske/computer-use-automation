"""Working out where to resume after a human has been driving.

The part of a handoff that is easy to get wrong and easy to skip. The operator has been operating
the application; the state has moved. Resuming blindly at the step that escalated would re-run work
they already did -- at best redundant, at worst a duplicate submission against a member's account.

### How the resume point is chosen

Scan forward from the step that escalated:

- A step whose **postcondition already holds** is treated as done, and skipped. Its declared effect
  is visible on the screen, which is the only evidence available that it happened.
- The first step that is not visibly done is where we resume -- provided its **precondition holds**.
- If that step's precondition does *not* hold, the session is somewhere the capability cannot act
  from, and we fail closed rather than guessing which step the operator actually left us at.

A step with **no postcondition cannot be skipped**, even if it looks done. There is no declared
evidence of its effect, so "probably done" is the only available conclusion, and that is exactly the
kind of assumption this system refuses to make. The cost is re-running a step; the alternative is
silently omitting one.

The diff of what changed is recorded either way: "record what the human did" is a requirement, and
it is also the only way to review a handoff afterwards.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from cua.domain.capability import Capability
from cua.domain.snapshot import UiSnapshot


@dataclass(frozen=True)
class HumanDelta:
    """What changed on the surface while a human held the session."""

    url_before: str
    url_after: str
    navigated: bool
    nodes_added: int
    nodes_removed: int
    values_changed: tuple[tuple[str, str, str], ...] = ()
    """(node_id, before, after) for controls whose value the operator altered."""

    @property
    def changed(self) -> bool:
        return bool(self.navigated or self.nodes_added or self.nodes_removed or self.values_changed)

    def summary(self) -> str:
        if not self.changed:
            return "the operator changed nothing on the surface"
        parts = []
        if self.navigated:
            parts.append(f"navigated {self.url_before} -> {self.url_after}")
        if self.values_changed:
            parts.append(f"{len(self.values_changed)} field(s) edited")
        if self.nodes_added or self.nodes_removed:
            parts.append(f"+{self.nodes_added}/-{self.nodes_removed} nodes")
        return "; ".join(parts)


def diff_snapshots(before: UiSnapshot, after: UiSnapshot) -> HumanDelta:
    """What the operator changed, in surface-neutral terms."""
    before_ids = {node.node_id for node in before.nodes}
    after_ids = {node.node_id for node in after.nodes}
    before_values = {node.node_id: node.value for node in before.nodes if node.value is not None}

    changed = tuple(
        (node.node_id, before_values.get(node.node_id) or "", node.value or "")
        for node in after.nodes
        if node.value is not None
        and node.node_id in before_values
        and before_values[node.node_id] != node.value
    )

    return HumanDelta(
        url_before=before.url,
        url_after=after.url,
        navigated=before.url != after.url,
        nodes_added=len(after_ids - before_ids),
        nodes_removed=len(before_ids - after_ids),
        values_changed=changed,
    )


@dataclass(frozen=True)
class Reconciliation:
    """Where to resume, or why we cannot."""

    resume_index: int | None
    reason: str
    skipped: tuple[str, ...] = field(default=())
    """Steps whose effect was already visible. Recorded so a reviewer can see what the operator
    completed on the system's behalf."""

    @property
    def can_resume(self) -> bool:
        return self.resume_index is not None


def reconcile(
    capability: Capability,
    snapshot: UiSnapshot,
    *,
    inputs: Mapping[str, Any],
    from_index: int,
) -> Reconciliation:
    """Decide where automation should pick up after a handoff.

    `from_index` is required rather than defaulting to 0, because scanning from the start is
    actively wrong here: once a flow has advanced, the early steps' preconditions no longer hold --
    the search box is not on the member record -- and a scan from zero would report that the session
    is unrecognizable when it is simply further along. The caller knows where it escalated; it has
    to say so.
    """
    skipped: list[str] = []

    for index in range(from_index, len(capability.steps)):
        step = capability.steps[index]

        if step.postcondition is not None and step.postcondition.evaluate(snapshot, inputs):
            skipped.append(step.id)
            continue

        if step.precondition is not None and not step.precondition.evaluate(snapshot, inputs):
            return Reconciliation(
                resume_index=None,
                reason=(
                    f"the session is not in a state step {step.id!r} can act from: it expects "
                    f"{step.precondition.describe()}. The operator may have left the application "
                    "somewhere this capability does not describe."
                ),
                skipped=tuple(skipped),
            )

        return Reconciliation(
            resume_index=index,
            reason=(
                f"resuming at step {step.id!r}"
                + (f"; {len(skipped)} step(s) already completed by the operator" if skipped else "")
            ),
            skipped=tuple(skipped),
        )

    # Every remaining step's effect is already visible: the operator finished the flow by hand.
    return Reconciliation(
        resume_index=len(capability.steps),
        reason="every remaining step is already satisfied; proceeding to the checkpoint",
        skipped=tuple(skipped),
    )
