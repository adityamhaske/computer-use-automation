# Evidence

Proof that the end-to-end thread actually ran. This directory is a required deliverable
(brief §6.3): a saved capability artifact plus logs from a discovery run and a replay run, including
at least one replay that hits an error or exceptional state.

> **Status: populated from Phase 06 onward.** Nothing here yet.

## Layout

```
capabilities/        the saved capability artifact(s) + tenant binding overlays
discovery/<run_id>/  the real LLM run that discovered the flow
replay/<run_id>/     deterministic replays: success, business outcome, and fault paths
escalation/<run_id>/ a stuck run, a human takeover, and the resume
evals/               stability and cross-tenant measurement reports
```

Each run directory contains:

| File | Contents |
|---|---|
| `trace.jsonl` | Append-only event stream: authorize / resolve / dispatch / observe, each tagged with actor, session, and lease epoch |
| `run_record.json` | The structured result: status, per-step resolutions, drift score, timings |
| `snapshots/` | The normalized `UiSnapshot` at each step |
| `screenshots/` | Redacted, with sensitive regions blurred |
| `llm_calls.jsonl` | *(discovery only)* the full model exchange, redacted outbound |
| `human_actions.jsonl`, `human_delta.json` | *(escalation only)* what the operator did, and how the state changed |

## Reading a run

Start with `run_record.json` — it carries the terminal status and, on failure, the step, what was
expected, and what was observed. `trace.jsonl` is the full story underneath it.

Every `dispatch` event has a matching `authorize` event; that correspondence is asserted by
`tests/invariants/test_policy_chokepoint.py`, and it is what demonstrates that nothing reached the
surface without passing policy — including anything a human did.

## On redaction

Everything here has passed through the redactor: logs, run records, snapshots, screenshots, and
outbound LLM prompts. Values are replaced while shapes are preserved
(`member_id=<redacted:string[5]>`), because a debugger needs to know a field was present and
well-formed.

`tests/invariants/test_redaction.py` asserts that seeded secrets and PII appear in **zero** sinks.
The mock application contains no real data of any kind.

## Committed vs generated

The reference runs referred to by `REPORT.md` are committed (force-added past `.gitignore`).
Everything a local run produces is ignored — use `make clean-evidence` to clear it.
