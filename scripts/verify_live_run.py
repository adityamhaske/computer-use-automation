"""Check that a discovery run in `evidence/` was driven by a live model, not a script.

The brief's one non-negotiable is a genuine LLM-driven run. This turns "read the trace and decide"
into a command:

    python scripts/verify_live_run.py                      # newest evidence/discovery/disc-*
    python scripts/verify_live_run.py evidence/discovery/disc-abc123

Hard checks (exit 1): a named, non-scripted model; tokens spent; every model call carrying a
gateway-issued request id. Soft check (a warning, exit 0): a median latency under 100 ms, which a
hosted model does not normally achieve -- worth a look, not proof of a fake, since a local gateway
with a warm cache can be fast.

Latency comes from each `llm_call`'s `latency_ms`. Runs recorded before that field existed fall back
to the gap since the previous trace event, which includes one page observation and so slightly
overstates the model's time; it is reported as "derived" so nobody mistakes it for a measurement.
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISCOVERY = ROOT / "evidence" / "discovery"


def newest_run() -> Path:
    runs = sorted(DISCOVERY.glob("disc-*"), key=lambda p: p.stat().st_mtime)
    if not runs:
        raise SystemExit(f"no disc-* runs under {DISCOVERY}")
    return runs[-1]


def verify(run_dir: Path) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    warnings: list[str] = []

    record = json.loads((run_dir / "run_record.json").read_text("utf-8"))
    events = [
        json.loads(line) for line in (run_dir / "trace.jsonl").read_text("utf-8").splitlines()
    ]
    calls = [e for e in events if e.get("event") == "llm_call"]

    model = str(record.get("model_name") or "")
    if not model or model.startswith("fake"):
        problems.append(f"model_name is {model!r}: this run was scripted, not live")
    if not record.get("tokens_used"):
        problems.append("tokens_used is empty: no model spend was recorded")
    if not calls:
        problems.append("the trace has no llm_call events")

    missing_id = [e["seq"] for e in calls if not e.get("request_id")]
    if missing_id:
        problems.append(f"llm_call events without a gateway request_id: seq {missing_id}")

    latencies: list[int] = []
    derived = False
    previous_at: datetime | None = None
    for event in events:
        at = datetime.fromisoformat(event["at"])
        if event.get("event") == "llm_call":
            if event.get("latency_ms") is not None:
                latencies.append(int(event["latency_ms"]))
            elif previous_at is not None:
                derived = True
                latencies.append(round((at - previous_at).total_seconds() * 1000))
        previous_at = at

    if latencies:
        median = statistics.median(latencies)
        kind = "derived from trace timestamps" if derived else "measured"
        print(f"      model latency: median {median:.0f} ms over {len(latencies)} call(s) ({kind})")
        if median < 100:
            warnings.append(
                f"median model latency is {median:.0f} ms; a hosted model rarely answers that "
                "fast. Confirm the gateway request ids against its own logs."
            )
    return problems, warnings


def main() -> int:
    run_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else newest_run()
    problems, warnings = verify(run_dir)

    label = run_dir.relative_to(ROOT) if run_dir.is_relative_to(ROOT) else run_dir
    for warning in warnings:
        print(f"WARN  {warning}")
    if problems:
        for problem in problems:
            print(f"FAIL  {problem}", file=sys.stderr)
        print(f"{label}: NOT verified as a live run", file=sys.stderr)
        return 1
    if warnings:
        print(f"{label}: passed the hard checks, with {len(warnings)} warning(s) above")
    else:
        print(f"{label}: verified as a live model run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
