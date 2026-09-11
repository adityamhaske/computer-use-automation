# Phase 11 — Evals & cross-tenant demo *(stretch)*

**Objective.** Measure what the write-up claims. Brief §8, stretch goals "multi-run stability" and
"cross-tenant reuse".

**Tests assert correctness; evals measure quality under variance.** The fault matrix and determinism
checks are *tests*, not evals — they are deterministic assertions, and calling them evals would
double the harness for nothing.

## Scope

| Suite | Metrics |
|---|---|
| `replay_stability` | Success rate over N runs; **replay determinism**; **wrong-action rate (target 0)**; **ambiguous-target refusal rate**; per-step **strategy used**; **fallback frequency** |
| `cross_tenant` | The same, on Variant B, plus **drift score**; demonstrates ladder descent `semantic_exact → structural_anchor` with CSS hints deliberately invalidated |
| `discovery` *(opt-in, spends tokens)* | Goal completion rate, steps, tokens, cost, latency. **Second thing to cut.** |

Plus `evals/runner.py`, `evals/scorers/`, `evals/report.py` → markdown + JSON into
`evidence/evals/`, and a `CapabilityEvaluation` document.

## The headline metric

**Wrong-action rate, target zero.** A system that refuses is acceptable — it escalates with full
context. A system that clicks the wrong thing in a core banking screen is an incident. Refusal rate
and wrong-action rate are reported *separately* and are not traded off against each other.

## Why cross-tenant is a measurement, not an assertion

Variant B relabels controls **and** invalidates every cached CSS hint while preserving row structure.
A run can therefore only succeed via `structural_anchor`. That makes "this system does not depend on
CSS selectors" falsifiable — if it did, Variant B would fail.

## Exit criteria

- [ ] Reports generated into `evidence/evals/`
- [ ] One artifact proven to replay on **both** variants
- [ ] Ladder descent measured and reported, not asserted
- [ ] `CapabilityEvaluation` emitted as a document separate from the capability
