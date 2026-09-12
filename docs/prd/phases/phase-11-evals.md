# Phase 11 — Evals & cross-tenant demo *(stretch — built)*

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

## What was actually built

Engine under `src/cua/evals/`, **not** at the repository root as this spec assumed. A harness that
drives a real browser has to live where the invariants reach it: `.importlinter` is rooted at `cua`,
the chokepoint AST scan walks `src/cua`, and mypy's strict mode covers the `cua` package. At the
root it would have escaped all three, and a harness that can reach around the chokepoint invalidates
the measurements it produces. The scan was extended to `scripts/` and `apps/` at the same time, to
catch the next module that tries.

The `discovery` suite was cut as planned -- it spends tokens to measure something the single
required live run already demonstrates.

## What the suites found

Three real defects, which is the argument for building measurement rather than asserting quality:

1. **The tenant overlay could not restate assertions.** `StepOverride` could retarget a control but
   not re-express the step's `precondition`, and `TenantBinding` could not replace the capability's
   `checkpoint`. Both assert the *recorded* institution's vocabulary, so every rebranded-tenant run
   failed closed on an assertion written for a different bank. The earlier test had only checked
   that the retargeted control resolved -- which it did. Fixed; cross-tenant replay is now 100%.
2. **Determinism was being judged across the whole suite** rather than per input set, so member
   12345's run was compared against member 99999's. They are supposed to differ. A healthy system
   was reported non-deterministic.
3. **Evidence directories were being reused without clearing**, so traces from a previous invocation
   concatenated onto the current one and runs appeared to have more steps than happened. The same
   mistake had already been made once in `make demo`; clearing now lives in `runtime/wiring.py`
   behind `fresh_run_dir()`, which is the seam both paths go through.

## Exit criteria

- [x] Reports generated into `evidence/evals/`
- [x] One artifact proven to replay on **both** variants
- [x] Ladder descent measured and reported, not asserted
- [x] `CapabilityEvaluation` emitted as a document separate from the capability
- [x] Wrong-action rate reported as 0, separately from refusal rate
