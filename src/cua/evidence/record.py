"""Projecting a trace into a `RunRecord`.

The record is *derived from the trace*, never accumulated alongside it. That is the whole point:
a record kept in parallel can disagree with the evidence, and the field most worth trusting --
whether every dispatch was authorized -- is exactly the one a parallel bookkeeper would get to
assert about itself. Here it is computed by matching each `dispatch` event to an `authorize` event
carrying the same `decision_id`, from the same append-only file
`tests/invariants/test_policy_chokepoint.py` reads.

So `RunRecord.unauthorized_dispatches` being empty is a statement about what is on disk, not about
what the executor believed it did.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from cua.domain.result import ObservationClass, RunResult
from cua.domain.run_record import ResolutionRecord, RunKind, RunRecord, StepRecord
from cua.domain.target import ResolutionStrategy
from cua.evidence.bus import EventType
from cua.policy.redact import Redactor

RECORD_FILENAME = "run_record.json"


def _enum(cls: Any, raw: Any) -> Any:
    """Parse an enum value from the trace, tolerating one written by a newer version."""
    if raw is None:
        return None
    try:
        return cls(raw)
    except ValueError:
        return None


def _at(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _resolution(event: dict[str, Any] | None) -> ResolutionRecord | None:
    if event is None:
        return None
    return ResolutionRecord(
        target_description=str(event.get("target", "")),
        strategy_used=_enum(ResolutionStrategy, event.get("strategy")),
        strategy_recorded=_enum(ResolutionStrategy, event.get("recorded_strategy")),
        resolved_node_id=event.get("node_id"),
        candidates_considered=int(event.get("candidates_considered") or 0),
    )


def build_run_record(
    run_dir: Path,
    *,
    kind: RunKind,
    result: RunResult | None = None,
    goal: str | None = None,
    tenant: str | None = None,
    model_name: str | None = None,
    resolver_config_hash: str | None = None,
) -> RunRecord:
    """Read `run_dir/trace.jsonl` and project it into a `RunRecord`."""
    trace = run_dir / "trace.jsonl"
    events: list[dict[str, Any]] = []
    if trace.exists():
        for line in trace.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))

    start = next((e for e in events if e["event"] == EventType.RUN_START.value), {})
    end = next(
        (
            e
            for e in reversed(events)
            if e["event"] in {EventType.RUN_END.value, EventType.ESCALATE.value}
        ),
        {},
    )

    # decision_id -> the authorization that granted it. A dispatch whose id is absent here reached
    # a surface unauthorized, which is an invariant violation rather than a missing field.
    granted = {
        e["decision_id"]: e
        for e in events
        if e["event"] == EventType.AUTHORIZE.value and e.get("granted") and e.get("decision_id")
    }

    steps: list[StepRecord] = []
    pending_resolve: dict[str, Any] | None = None
    current_step: str | None = None
    current_observation: ObservationClass | None = None
    recovery_rule: str | None = None

    for event in events:
        name = event["event"]
        if name == EventType.RESOLVE.value:
            pending_resolve = event
        elif name == EventType.CLASSIFY.value:
            current_step = event.get("step") or current_step
            current_observation = _enum(ObservationClass, event.get("observation"))
        elif name == EventType.RECOVERY.value:
            recovery_rule = event.get("rule") or event.get("id")
        elif name == EventType.DISPATCH.value:
            decision_id = event.get("decision_id")
            authorization = granted.get(decision_id) if decision_id else None
            steps.append(
                StepRecord(
                    index=len(steps),
                    step_id=current_step or str(event.get("action_type", "?")),
                    action_type=str(event.get("action_type", "?")),
                    actor=event.get("actor", "automation"),
                    lease_epoch=int(event.get("lease_epoch") or 0),
                    authorized=authorization is not None,
                    policy_decision=(authorization or {}).get("reason"),
                    observation=current_observation,
                    resolution=_resolution(pending_resolve),
                    started_at=_at(event.get("at")),
                    duration_ms=int(event.get("duration_ms") or 0),
                    recovery_rule_applied=recovery_rule,
                    error_code=None if event.get("ok") else str(event.get("message") or "")[:200],
                )
            )
            pending_resolve = None
            recovery_rule = None

    started_at = _at(start.get("at"))
    finished_at = _at(end.get("at"))
    duration = (
        int((finished_at - started_at).total_seconds() * 1000)
        if started_at and finished_at
        else (result.duration_ms if result else 0)
    )

    # Token accounting, derived from the trace rather than threaded through the caller -- the record
    # is a projection of what happened, and keeping it one keeps it honest. `tokens_used` is
    # documented as the field that shows a run actually spent a model, and nothing populated it, so
    # every record read `null` whether or not a model had run. A run with no `llm_call` events
    # stays `None`: zero and "no model was involved" are different claims.
    llm_calls = [e for e in events if e["event"] == EventType.LLM_CALL.value]
    tokens_used = (
        sum(
            int(e.get("prompt_tokens") or 0) + int(e.get("completion_tokens") or 0)
            for e in llm_calls
        )
        if llm_calls
        else None
    )
    # The model the *provider* reported, which a scripted port cannot fabricate as easily as the
    # requested name it was handed.
    observed_model = next((str(e["model"]) for e in llm_calls if e.get("model")), None)

    return RunRecord(
        run_id=str(start.get("run_id") or run_dir.name),
        kind=kind,
        capability_ref=start.get("capability"),
        capability_content_hash=start.get("content_hash"),
        tenant=tenant,
        goal=goal or start.get("goal"),
        inputs=start.get("inputs") or {},
        steps=tuple(steps),
        result=result,
        started_at=started_at,
        duration_ms=duration,
        evidence_ref=str(run_dir),
        model_name=model_name or observed_model or start.get("model"),
        tokens_used=tokens_used,
        resolver_config_hash=resolver_config_hash,
        human_actions=sum(1 for s in steps if str(s.actor) == "human"),
        lease_transitions=tuple(
            str(e.get("transition"))
            for e in events
            if e["event"] == EventType.LEASE.value and e.get("transition")
        ),
    )


def write_run_record(
    record: RunRecord,
    run_dir: Path,
    *,
    redactor: Redactor,
    sensitive_keys: frozenset[str] = frozenset(),
) -> Path:
    """Write the record next to the trace it was derived from, redacted the same way.

    The redactor is required rather than optional, and that is the point of the signature. This
    function wrote `model_dump_json()` straight to disk, so the evidence directory held two sinks
    for the same run with different rules: `trace.jsonl` emitted output *names* only and masked
    everything through the bus, while `run_record.json` beside it carried the values in the clear.

    `result` is where that mattered. It is passed in live by the executor rather than read back from
    the redacted trace, so `outputs`, `outcome.data` and `error.expected/observed` -- all built from
    page content -- had never been through a redactor at all. An output declared `sensitive: true`
    was masked in one file and printed in the other.
    """
    path = run_dir / RECORD_FILENAME
    payload = redactor.structure(record.model_dump(mode="json"), sensitive_keys=sensitive_keys)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
