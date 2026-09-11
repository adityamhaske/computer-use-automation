# Phase 10 — Capability catalog *(stretch — cut first)*

**Objective.** Expose saved capabilities as a catalog an AI agent can discover and invoke by name
with typed arguments. Brief §8, stretch goal 1.

**Status: the first thing to cut.** It is the only fully droppable module in the plan. Nothing on
the critical path depends on it.

## Scope

| Module | Responsibility |
|---|---|
| `catalog/store.py` | `CapabilityStore` — list, load, resolve `id@version` |
| `catalog/overlay.py` | `base ⊕ tenant_binding` → effective capability *(also needed by Phase 11)* |
| `catalog/toolspec.py` | Capability → JSON Schema tool definition |
| `cli/catalog.py` | `cua catalog list \| show \| invoke` |

## Design note

`toolspec.py` is nearly free because the artifact's `inputs`/`outputs` already emit JSON Schema —
that was the point of typing them (ADR 0002). **The artifact is the tool contract**; there is no
second source of truth to drift.

## Exit criteria

- [ ] `cua catalog list` shows discovered capabilities with their typed signatures
- [ ] `cua catalog invoke <id> --input member_id=12345` runs a deterministic replay
- [ ] A tiny "calling agent" demo invokes a capability by name and handles all four run statuses

## Cut plan

If cut, `catalog/overlay.py` **still gets built** in Phase 11 — tenant overlay resolution is brief
§3.7 and is not optional. Only the store, tool-spec export, and CLI go. REPORT.md §7 says so.
