# Design write-up

> **Status: in progress.** This document is assembled from the ADRs in
> [`docs/adr/`](docs/adr/), which are written as decisions are made rather than reconstructed
> afterwards. The seven headings below are the ones the brief mandates; each currently links to the
> decision record that will back it.

## 1. Architecture

*To be written in Phase 12.* Backing material:
[ADR 0001 — `UiSnapshot` as the cross-surface abstraction](docs/adr/0001-uisnapshot-as-the-cross-surface-abstraction.md),
[ADR 0004 — single policy chokepoint](docs/adr/0004-single-policy-chokepoint.md),
[PRD](docs/prd/00-prd.md).

## 2. Artifact schema

*To be written in Phase 12.* Backing material:
[ADR 0002 — immutable, content-addressed capability](docs/adr/0002-immutable-content-addressed-capability.md),
[design/artifact-schema.md](docs/design/artifact-schema.md).

## 3. Determinism & error handling

*To be written in Phase 12.* Backing material:
[ADR 0003 — four-class observation taxonomy](docs/adr/0003-four-class-observation-taxonomy.md),
[design/error-taxonomy.md](docs/design/error-taxonomy.md),
[design/target-resolution.md](docs/design/target-resolution.md).

## 4. Heterogeneity & multi-tenant

*To be written in Phase 12.* Backing material:
[ADR 0001](docs/adr/0001-uisnapshot-as-the-cross-surface-abstraction.md),
[ADR 0005 — tenant overlay, not fork](docs/adr/0005-tenant-overlay-not-fork.md).

## 5. Escalation & handoff

*To be written in Phase 12.* Backing material:
[design/control-transfer.md](docs/design/control-transfer.md).

## 6. Safety

*To be written in Phase 12.* Backing material:
[ADR 0004](docs/adr/0004-single-policy-chokepoint.md), [`config/policy.yaml`](config/policy.yaml).

## 7. Cuts

*To be written in Phase 12.* Known deliberate cuts so far, each documented where it was made:

- Desktop surface — stubbed at the `SurfaceDriver` port, interface only (ADR 0001)
- Multi-tenant infrastructure — modeled as an overlay document, demonstrated on one variant (ADR 0005)
- Operator console — single-operator, no auth, no routing (design/control-transfer.md)
- Capability catalog — stretch goal, first to be cut (docs/prd/phases/phase-10-catalog.md)
- Assisted LLM fallback on replay failure — designed, not built; it would breach the
  no-LLM-in-replay invariant without a separate explicitly-policed path
