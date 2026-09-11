# Phase 02 — Domain model & artifact schema ⭐

**Objective.** The centerpiece. Typed, immutable, content-addressed domain types with the artifact
schema at the center.

**Why it matters most.** The graders weigh system design first and say "the artifact schema and
replay contract are central." This phase is what they read first.

Reference: [design/artifact-schema.md](../../design/artifact-schema.md),
[ADR 0002](../../adr/0002-immutable-content-addressed-capability.md),
[ADR 0003](../../adr/0003-four-class-observation-taxonomy.md).

## Scope

`src/cua/domain/`, pure — no I/O, no clock, no browser:

| Module | Types |
|---|---|
| `capability.py` | `Capability` (frozen, content-hashed), `Step`, `Checkpoint`, `Outcome`, `RecoveryRule`, `InputSpec`, `OutputSpec`, `Provenance` |
| `target.py` | `TargetDescriptor`, `NameMatch`, `Scope`, `Anchor`, `Hints`, `ResolutionStrategy` |
| `action.py` | The closed action space incl. `raw_input`; `ActionRisk` |
| `snapshot.py` | `UiSnapshot`, `UiNode` |
| `result.py` | `ObservationClass`, `RunStatus`, `FailureCode`, `RunResult` |
| `run_record.py` | `RunRecord`, `StepRecord`, `ResolutionRecord` |
| `evaluation.py` | `CapabilityEvaluation` |
| `approval.py` | `CapabilityApproval` |
| `tenant_binding.py` | `TenantBinding` |
| `actor.py` | `Actor`, `LeaseView` |

Plus: YAML (de)serialization, JSON Schema export, `content_hash` computation.

## Non-scope

Anything that executes. This phase is types and their laws.

## Exit criteria

- [ ] `Capability` round-trips YAML → model → YAML byte-identically
- [ ] JSON Schema export is golden-tested
- [ ] **`Capability` has no mutable runtime field** — no stability, no approval, no last-run
- [ ] `content_hash` verified on load; a tampered artifact is rejected
- [ ] `.importlinter` `pure-domain` passes with real imports present
- [ ] A hand-written reference artifact for the mock-app flow validates

## Risks

| Risk | Mitigation |
|---|---|
| Schema churn once replay meets reality | Expected and healthy. Version the schema from day one (`schema_version`) and treat Phase 08 feedback as the real design review. |
| Over-modelling — types nobody uses | Every type must be exercised by the demo path. If it isn't, delete it. |
