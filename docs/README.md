# Documentation map

Start with [`/README.md`](../README.md) to run the system, and [`/REPORT.md`](../REPORT.md) for the
design write-up. This tree is the supporting detail, and it is also published, rendered and
cross-linked, at the **[documentation site](https://adityamhaske.github.io/interface.ai/)** — which
is where the CLI reference, the demo walkthrough and the getting-started material live now that the
root README is deliberately short.

## Decisions — *why it is this way*

| ADR | Decision |
|---|---|
| [0001](adr/0001-uisnapshot-as-the-cross-surface-abstraction.md) | `UiSnapshot` is the cross-surface abstraction; accessibility trees are a driver detail |
| [0002](adr/0002-immutable-content-addressed-capability.md) | The capability artifact is immutable and content-addressed |
| [0003](adr/0003-four-class-observation-taxonomy.md) | Four-class observation taxonomy, separate from run status |
| [0004](adr/0004-single-policy-chokepoint.md) | A single, type-enforced policy chokepoint for every actor |
| [0005](adr/0005-tenant-overlay-not-fork.md) | Tenants overlay a capability; they never fork it |

## Design — *how it works*

| Document | Covers |
|---|---|
| [artifact-schema.md](design/artifact-schema.md) | The capability artifact and its sibling documents |
| [target-resolution.md](design/target-resolution.md) | The resolution ladder, determinism rules, ambiguity refusal, drift |
| [error-taxonomy.md](design/error-taxonomy.md) | Observation classes, run statuses, failure codes, the fault matrix |
| [control-transfer.md](design/control-transfer.md) | The lease, escalation, policed human input, re-anchoring resume |

## Design system — *how it should look*

| Document | Covers |
|---|---|
| [UIUX/README.md](UIUX/README.md) | The design system for every web surface (console, site) — start here |
| [UIUX/principles.md](UIUX/principles.md) | What "good" means here, and the four rules that follow |
| [UIUX/design-tokens.md](UIUX/design-tokens.md) | Exact colors, type scale, spacing, radius, motion |
| [UIUX/components.md](UIUX/components.md) | Buttons, cards, tables, forms, status, dialogs, toasts |
| [UIUX/patterns.md](UIUX/patterns.md) | Page shells, navigation, responsive rules, live data |
| [UIUX/accessibility.md](UIUX/accessibility.md) | The non-negotiable minimum, checked before shipping |
| [UIUX/workflow.md](UIUX/workflow.md) | Where UI files live, how to add/fix a page, the review checklist |

## Plan — *what we are building, in what order*

| Document | Covers |
|---|---|
| [prd/00-prd.md](prd/00-prd.md) | Problem, users, requirements, success criteria, scope |
| [prd/01-requirements-traceability.md](prd/01-requirements-traceability.md) | Every brief requirement → module → test → evidence |
| [prd/phases/](prd/phases/) | One short doc per phase: objective, work list, exit criteria |

## Operating

| Document | Covers |
|---|---|
| [testing/strategy.md](testing/strategy.md) | The claim → test table; what runs offline and what costs tokens |
| [runbooks/](runbooks/) | Running a discovery, running a replay, handling an escalation |

## Conventions

[`/AGENTS.md`](../AGENTS.md) is the working agreement: the nine enforced invariants, the
architecture map, and the scope discipline. Read it before editing code.
