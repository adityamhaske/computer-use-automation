# ADR 0002 — The capability artifact is immutable and content-addressed

- **Status:** Accepted
- **Date:** 2026-09-11
- **Context:** Brief §3.2 ("versioned and reviewable"), §7 (the artifact schema is graded first)

## Context

A capability artifact accumulates pressure to hold more than its contract. Natural candidates:
how often it replayed successfully, whether a reviewer approved it, when it last ran, which tenants
use it. All of that is genuinely useful. The question is whether it belongs *in the definition*.

An earlier draft of this design put `provenance.stability = {runs, success, score}` and
`provenance.approval` inside the capability. Two problems surfaced immediately:

1. The definition mutates every time you run it, so a git diff between two versions is dominated by
   telemetry rather than by the change in behaviour. It stops being reviewable, which is the one
   property §3.2 explicitly asks for.
2. "Which version of this capability produced that run?" becomes unanswerable, because the version
   you would compare against has been overwritten by the run itself.

## Decision

**A `Capability` at `id@version` is frozen and content-hashed. Nothing that changes as a result of
executing it may live inside it.**

Runtime and governance state move into separate documents keyed by `id@version`:

| Document | Holds | Mutable? |
|---|---|---|
| `cua.capability/v1` | Steps, typed inputs/outputs, checkpoint, outcomes, recovery, policy, discovery provenance | **No** |
| `cua.run_record/v1` | One execution: status, step trace, resolution strategies, drift, timings, evidence refs | append-only |
| `cua.capability_evaluation/v1` | Aggregate over many runs: stability, strategy mix, drift trend | derived, recomputable |
| `cua.capability_approval/v1` | `draft → approved`, approver, scope, expiry | yes — it is *about* the version, not part of it |

Discovery provenance (`discovered_by`, `transcript_ref`) **stays** in the capability: it describes
how this frozen thing came to exist and never changes.

`content_hash` covers the whole document, and for a tenant-bound capability it covers the resolved
base ⊕ overlay — so a tenant override produces a distinguishable effective capability.

## Consequences

**Good.** Reviewing a capability is reviewing a contract. The `draft → approved` gate has something
stable to attach to. A `RunRecord` can name exactly what it executed. Stability scoring becomes a
derived view that can be recomputed or thrown away without touching the definition.

**Bad.** More document types, and a join to answer "is this capability reliable?" For a system with
files on disk that is a trivially acceptable cost; at scale it is the normal shape.

**Also good, unexpectedly.** Immutability makes the multi-tenant story cleaner: a tenant cannot
"drift" a shared capability by using it, only by declaring an explicit overlay.
