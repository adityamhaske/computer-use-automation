# Requirements traceability

Every requirement in the assignment brief §3, mapped to the module that satisfies it, the test that
proves it, and the evidence that demonstrates it.

A row with no test is a claim. A row with no evidence is a demo that never ran.

| Brief | Requirement | Module | Test | Evidence |
|---|---|---|---|---|
| **3.1** | Accept goal + target | `cua/cli`, `cua/agent/loop.py` | `unit/test_cli_args.py` | `evidence/discovery/*/run_record.json` |
| 3.1 | LLM observe→decide→act loop against a live surface | `cua/agent/loop.py` | `integration/test_discovery_fake_llm.py`, `e2e/test_live_discovery.py` *(live)* | `evidence/discovery/*/trace.jsonl`, `llm_calls.jsonl` |
| 3.1 | Stopping conditions (max steps, timeout, dead-end) | `cua/agent/stop.py` | `unit/test_stop_conditions.py` | `evidence/discovery/*/run_record.json` |
| 3.1 | Works without a clean DOM | `cua/perception`, `cua/targeting` | `integration/test_variant_b.py` | `evidence/evals/cross_tenant.md` |
| **3.2** | Ordered steps | `domain/capability.py` | `contract/test_capability_schema.py` | `evidence/capabilities/*.yaml` |
| 3.2 | How each control is identified | `domain/target.py`, `cua/targeting` | `unit/test_resolver_*.py` | `evidence/capabilities/*.yaml` |
| 3.2 | Typed input parameters | `domain/capability.py` | `contract/test_json_schema_export.py` | `evidence/capabilities/*.yaml` |
| 3.2 | Typed outputs / data to extract | `domain/capability.py`, `cua/replay/extract.py` | `integration/test_replay_outputs.py` | `evidence/replay/*/run_record.json` |
| 3.2 | Checkpoint / success condition | `cua/replay/checkpoint.py` | `integration/test_checkpoint.py` | `evidence/replay/*/run_record.json` |
| 3.2 | Versioned and reviewable | `domain/capability.py` | `contract/test_capability_immutability.py` | `evidence/capabilities/*.yaml` |
| 3.2 | Decoupled from the raw model transcript | `domain/capability.py` (`transcript_ref`) | `contract/test_capability_schema.py` | `evidence/capabilities/*.yaml` |
| **3.3** | Replay without the LLM in the decision loop | `cua/replay` | **`invariants/test_no_llm_in_replay.py`**, `.importlinter` | `evidence/replay/*/run_record.json` |
| 3.3 | Stable element targeting | `cua/targeting/resolver.py` | `unit/test_resolver_ladder.py` | `evidence/replay/*/run_record.json` (`strategy_used`) |
| 3.3 | Verify checkpoint, return declared outputs | `cua/replay/executor.py` | `integration/test_replay_outputs.py` | `evidence/replay/*/run_record.json` |
| 3.3 | Business outcomes vs recoverable vs hard failure | `domain/result.py`, `cua/replay/classify.py` | **`integration/test_fault_matrix.py`** | `evidence/replay/*-not-found/`, `*-fault/` |
| 3.3 | Bounded recovery | `cua/replay/recovery.py` | `integration/test_fault_matrix.py` | `evidence/replay/*-fault/run_record.json` |
| 3.3 | Fail closed on unknown states | `cua/replay/classify.py` | **`integration/test_fail_closed.py`** | `evidence/escalation/*/` |
| 3.3 | Structured, debuggable result | `domain/result.py` | `unit/test_result_contract.py` | `evidence/replay/*/run_record.json` |
| **3.4** | Configurable allowlist, enforced | `cua/policy/allowlist.py` | **`invariants/test_policy_chokepoint.py`** | `evidence/*/trace.jsonl` (authorize events) |
| 3.4 | Safe vs risky/irreversible actions | `cua/policy/risk.py` | `unit/test_risk_classifier.py` | `evidence/capabilities/*.yaml` (`risk:`) |
| 3.4 | Never persist secrets or raw PII | `cua/policy/redact.py` | **`invariants/test_redaction.py`** | all of `evidence/` (asserted clean) |
| **3.5** | Structured log of what and why | `cua/evidence/bus.py` | `unit/test_evidence_bus.py` | `evidence/*/trace.jsonl` |
| 3.5 | Richer signal on failure | `cua/evidence/capture.py` | `integration/test_failure_capture.py` | `evidence/*/screenshots/`, `snapshots/` |
| **3.6** | Detect stuck, route intervention with context | `cua/hitl/intervention.py` | `integration/test_escalation.py` | `evidence/escalation/*/intervention.json` |
| 3.6 | Human takes control of the **live** session | `cua/hitl/broker.py`, `cua/hitl/console/` | `integration/test_handoff.py` | `evidence/escalation/*/human_actions.jsonl` |
| 3.6 | Hand control back; run resumes | `cua/hitl/broker.py`, `cua/replay/executor.py` | `integration/test_reanchor_resume.py` | `evidence/escalation/*/human_delta.json` |
| 3.6 | Know who is in control | `cua/hitl/lease.py` | `unit/test_lease.py`, **`invariants/test_human_control_safety.py`** | `evidence/*/trace.jsonl` (`actor`, `lease_epoch`) |
| **3.7** | Surface abstraction extends to legacy/desktop | `cua/surfaces/` port, `desktop_uia/` stub | `.importlinter` `surface-neutral-targeting` | `REPORT.md` §4, ADR 0001 |
| 3.7 | Multi-tenant reuse without re-recording | `domain/tenant_binding.py`, `cua/catalog/overlay.py` | **`integration/test_variant_b.py`** | `evidence/evals/cross_tenant.md` |
| 3.7 | Detect and manage per-tenant drift | `cua/targeting/drift.py` | `unit/test_drift.py` | `evidence/replay/*/run_record.json` (`drift_score`) |

## Deliverables

| Brief §6 | Required path | Status |
|---|---|---|
| 1 | `/README.md` — setup, config, demo path | Phase 12 |
| 2 | `/REPORT.md` — the seven mandated headings | Phase 12 |
| 3 | `/evidence/` — artifact + discovery log + replay log + an error-path replay | Phases 06–09, indexed in 12 |

## Stretch goals (brief §8) — at most two, depth over breadth

| Goal | Status |
|---|---|
| Agent-facing capability catalog | Phase 10 — **first to be cut** |
| Confidence & approval gating | Partly core: `CapabilityApproval` gates irreversible replay |
| Canonicalization / cross-tenant reuse | Phase 11 — Variant B demo |
| Multi-run stability signal | Phase 11 — `replay_stability` eval |
| Code generation from an artifact | Not planned |
| Assisted LLM fallback on replay failure | Designed, not built — would breach the no-LLM-in-replay invariant without a separate, explicitly-policed path |
