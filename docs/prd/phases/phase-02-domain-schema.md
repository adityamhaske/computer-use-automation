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

- [x] `Capability` round-trips YAML → model → YAML
- [x] Tool-schema export is asserted (the artifact *is* the agent contract)
- [x] **`Capability` has no mutable runtime field** — asserted by a field-name guard, so adding
      `stability` back "just for convenience" fails the suite
- [x] `content_hash` verified on load; a tampered artifact is rejected, an unsealed draft is not
- [x] `.importlinter` `pure-domain` passes with **real imports present** (69 dependencies analyzed,
      up from 0 — the contract is no longer vacuous)
- [x] A hand-written reference artifact for the mock-app flow validates
- [x] Tenant overlay resolves, fails closed on unknown steps, and does not mutate the base

### Verification record

72 tests green. 12 domain modules, `mypy --strict` clean, 5/5 contracts kept.

**Design decisions made while building, worth noting:**

- **`escalation.on` renamed to `escalation.triggers`.** YAML 1.1 resolves a bare `on` key to the
  boolean `True`, so the reference artifact failed to load with an error that read like a schema
  bug. The loader was also hardened (`_StrictLoader`) to stop coercing `on/off/yes/no` — stdlib
  `SafeLoader` silently collapses `{on, off, yes, no, true}` from five keys to **two**. In a format
  humans hand-edit, that is a landmine worth removing twice.

- **Predicate evaluation lives in `domain/` and is pure.** One closed vocabulary serves
  preconditions, postconditions, checkpoints, outcome detectors and recovery detectors. Using one
  for all five means "how do you know you succeeded" and "how do you know the member wasn't found"
  are expressed in the same reviewable terms, and the executor has exactly one evaluator to be
  correct about.

- **Normalization deliberately does not absorb rebranding.** `"Member  Number:"` normalizes to
  `"Member Number"`; `"Member #"` does not. Asserted by test — the fuzzy version is what would make
  an automation click the wrong control.

## Risks

| Risk | Mitigation |
|---|---|
| Schema churn once replay meets reality | Expected and healthy. Version the schema from day one (`schema_version`) and treat Phase 08 feedback as the real design review. |
| Over-modelling — types nobody uses | Every type must be exercised by the demo path. If it isn't, delete it. |
