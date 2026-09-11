"""Richer evidence when something goes wrong.

Lives in `cua.runtime` rather than `cua.evidence` because it touches a `SurfaceDriver` -- it needs
a screenshot and node geometry. Only `cua.runtime` may do that.

It was written in `cua.evidence` first, and `tests/invariants/test_policy_chokepoint.py` caught it.
The rule's value is that it is absolute: a carve-out for "read-only driver methods are fine" would
be defensible in isolation and would immediately make the invariant something to reason about
case-by-case rather than something to check. Moving the module is cheaper than weakening the rule.

Brief §3.5 asks for a structured log plus at least one richer signal on failure. The structured log
is `EvidenceBus`; this is the richer signal: a redacted screenshot and the full snapshot at the
moment of failure.

Capturing *both* is deliberate. A screenshot shows what a human would have seen; a snapshot shows
what the system actually perceived. When a replay fails on an unrecognized screen, the interesting
question is usually the gap between those two -- a control that is plainly visible in the image but
absent from the tree is a perception bug, not a targeting bug, and the two are fixed in different
places.
"""

from __future__ import annotations

from dataclasses import dataclass

from cua.domain.capability import Capability
from cua.domain.snapshot import Rect, UiSnapshot
from cua.evidence.bus import EventType, EvidenceBus
from cua.surfaces.base import SurfaceDriver


@dataclass(frozen=True)
class CaptureRefs:
    snapshot_ref: str | None = None
    screenshot_ref: str | None = None


@dataclass
class FailureCapture:
    """Captures the state around a failure, with sensitive regions obscured."""

    driver: SurfaceDriver
    evidence: EvidenceBus

    def sensitive_regions(
        self, snapshot: UiSnapshot, capability: Capability | None
    ) -> tuple[Rect, ...]:
        """Bounding boxes to paint over before the screenshot is taken.

        Driven by the capability's own declarations: an input or output marked `sensitive: true` is
        located in the snapshot and its geometry fetched. Nothing is guessed -- a screenshot cannot
        be redacted by pattern matching, so the declaration is the only reliable signal.
        """
        if capability is None:
            return ()

        sensitive_names = {spec.name for spec in capability.inputs if spec.sensitive} | {
            spec.name for spec in capability.outputs if spec.sensitive
        }
        if not sensitive_names:
            return ()

        node_ids = tuple(
            node.node_id
            for node in snapshot.nodes
            if node.name and any(name.lower() in node.name.lower() for name in sensitive_names)
        )
        if not node_ids:
            return ()
        return tuple(self.driver.bounds_for(node_ids).values())

    def capture(
        self,
        snapshot: UiSnapshot,
        *,
        label: str,
        capability: Capability | None = None,
        reason: str = "",
    ) -> CaptureRefs:
        """Save a snapshot and a redacted screenshot.

        Best-effort by design: a browser that has already crashed cannot produce a screenshot, and
        losing the whole evidence record because the richer half failed would be exactly backwards
        -- the structured trace is the part that must survive.
        """
        snapshot_ref = self.evidence.save_snapshot(snapshot, label)

        screenshot_ref: str | None = None
        try:
            image = self.driver.screenshot(redact=self.sensitive_regions(snapshot, capability))
            screenshot_ref = self.evidence.save_screenshot(image, label)
        except Exception as exc:
            self.evidence.emit(
                EventType.NOTE,
                note="screenshot capture failed",
                error=f"{type(exc).__name__}: {exc}",
            )

        self.evidence.emit(
            EventType.NOTE,
            note=f"failure capture: {label}",
            reason=reason,
            url=snapshot.url,
            snapshot_ref=snapshot_ref,
            screenshot_ref=screenshot_ref,
        )
        return CaptureRefs(snapshot_ref=snapshot_ref, screenshot_ref=screenshot_ref)
