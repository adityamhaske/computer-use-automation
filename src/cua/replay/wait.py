"""Waiting on conditions, never on durations.

`time.sleep(2)` passes on a developer's machine and fails on a loaded CI runner -- the worst kind of
flake, because it looks like a real failure just often enough to be ignored, and because the fix is
always "make it 3".

Everything here polls a *condition* with a declared timeout. The small interval between polls is
not a wait: the loop exits the moment the condition holds, so a fast page costs one interval and a
slow one costs no more than the declared budget. A timeout is a **result** -- reported as
`TIMEOUT` -- not a reason to proceed and hope.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from cua.domain.capability import WaitFor, WaitPolicy
from cua.domain.snapshot import UiSnapshot

POLL_INTERVAL_S = 0.05


@dataclass(frozen=True)
class WaitOutcome:
    satisfied: bool
    snapshot: UiSnapshot
    waited_ms: int
    reason: str = ""


def wait_for(
    policy: WaitPolicy,
    *,
    observe: Callable[[], UiSnapshot],
    inputs: Mapping[str, Any],
    before: UiSnapshot | None = None,
) -> WaitOutcome:
    """Poll until the policy's condition holds, or its timeout expires."""
    started = time.monotonic()
    deadline = started + policy.timeout_ms / 1000
    previous: UiSnapshot | None = None
    snapshot = observe()

    while True:
        if policy.for_ is WaitFor.PREDICATE and policy.until is not None:
            if policy.until.evaluate(snapshot, inputs):
                return _done(True, snapshot, started)

        elif policy.for_ is WaitFor.NAVIGATION:
            # A legacy app navigates by replacing the document, so a changed URL or a materially
            # different node count both count. Waiting only on the URL misses a frameset app that
            # re-renders the content frame in place -- which is most of this flow.
            if before is not None and (
                snapshot.url != before.url or len(snapshot.nodes) != len(before.nodes)
            ):
                return _done(True, snapshot, started)

        else:  # SNAPSHOT_STABLE
            # Two consecutive identical observations. Cheap, surface-neutral, and it does not
            # require the driver to expose a network-idle notion that a desktop surface would not
            # have.
            if previous is not None and _same(previous, snapshot):
                return _done(True, snapshot, started)

        if time.monotonic() >= deadline:
            return WaitOutcome(
                satisfied=False,
                snapshot=snapshot,
                waited_ms=int((time.monotonic() - started) * 1000),
                reason=f"{policy.for_.value} not satisfied within {policy.timeout_ms}ms",
            )

        time.sleep(POLL_INTERVAL_S)
        previous, snapshot = snapshot, observe()


def _same(left: UiSnapshot, right: UiSnapshot) -> bool:
    if left.url != right.url or len(left.nodes) != len(right.nodes):
        return False
    return all(
        a.node_id == b.node_id and a.name == b.name and a.value == b.value
        for a, b in zip(left.nodes, right.nodes, strict=True)
    )


def _done(satisfied: bool, snapshot: UiSnapshot, started: float) -> WaitOutcome:
    return WaitOutcome(
        satisfied=satisfied, snapshot=snapshot, waited_ms=int((time.monotonic() - started) * 1000)
    )
