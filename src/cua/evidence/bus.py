"""The run record: append-only, redacted, actor-tagged.

This is what makes a run debuggable without reproducing it -- the brief's requirement -- and what
makes the policy chokepoint *checkable* rather than merely claimed. Every dispatch here must have a
matching authorization, and a test asserts exactly that against real evidence files.

Append-only JSONL because it survives a crash mid-run. If the process dies, everything up to that
point is already on disk and readable, which is precisely the run you most want to look at.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from cua.domain.actor import Actor
from cua.domain.snapshot import UiSnapshot
from cua.policy.redact import Redactor


class EventType(StrEnum):
    RUN_START = "run_start"
    OBSERVE = "observe"
    AUTHORIZE = "authorize"
    RESOLVE = "resolve"
    DISPATCH = "dispatch"
    CLASSIFY = "classify"
    RECOVERY = "recovery"
    ESCALATE = "escalate"
    LEASE = "lease"
    LLM_CALL = "llm_call"
    RUN_END = "run_end"
    NOTE = "note"


@dataclass
class EvidenceBus:
    """Writes one run's evidence into its own directory."""

    run_dir: Path
    redactor: Redactor
    run_id: str
    sensitive_keys: frozenset[str] = frozenset()
    """Input names the capability declared sensitive. Masked by shape wherever they appear."""

    _sequence: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        (self.run_dir / "snapshots").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "screenshots").mkdir(parents=True, exist_ok=True)

    @property
    def trace_path(self) -> Path:
        return self.run_dir / "trace.jsonl"

    def emit(
        self,
        event: EventType,
        *,
        actor: Actor = Actor.SYSTEM,
        lease_epoch: int = 0,
        **payload: Any,
    ) -> int:
        """Append one event. Everything is redacted on the way in, never on the way out.

        Redacting at write time rather than at read time matters: an evidence file that is redacted
        when displayed has still been written to disk in the clear.
        """
        self._sequence += 1
        record = {
            "seq": self._sequence,
            "at": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "event": event.value,
            "actor": actor.value,
            "lease_epoch": lease_epoch,
            **self.redactor.structure(payload, sensitive_keys=self.sensitive_keys),
        }
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
        return self._sequence

    def save_snapshot(self, snapshot: UiSnapshot, label: str) -> str:
        """Persist a snapshot for post-hoc inspection.

        Worth the disk: when a replay fails on an unrecognized screen, the snapshot is the only
        record of what the system actually perceived -- as opposed to what the page looked like.
        """
        path = self.run_dir / "snapshots" / f"{self._sequence:04d}-{label}.json"
        payload = self.redactor.structure(
            snapshot.model_dump(mode="json"), sensitive_keys=self.sensitive_keys
        )
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return str(path.relative_to(self.run_dir))

    def save_screenshot(self, image: bytes, label: str) -> str:
        """Persist a screenshot. Already redacted by the driver, before the bytes existed."""
        path = self.run_dir / "screenshots" / f"{self._sequence:04d}-{label}.png"
        path.write_bytes(image)
        return str(path.relative_to(self.run_dir))

    def read_events(self) -> list[dict[str, Any]]:
        """Read the trace back -- used by tests and by the run-record builder."""
        if not self.trace_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def unauthorized_dispatches(self) -> list[dict[str, Any]]:
        """Dispatch events with no matching authorization.

        Must always be empty. This is the reconciliation that turns "nothing reaches a surface
        without passing policy" from an assertion into something checkable from the artifact a run
        leaves behind -- including for anything a human did.
        """
        events = self.read_events()
        authorized = {
            event.get("decision_id")
            for event in events
            if event["event"] == EventType.AUTHORIZE.value and event.get("granted")
        }
        return [
            event
            for event in events
            if event["event"] == EventType.DISPATCH.value
            and event.get("decision_id") not in authorized
        ]
