# Phase 10 — Capability catalog *(stretch — cut, then built)*

**Objective.** Expose saved capabilities as a catalog an AI agent can discover and invoke by name
with typed arguments. Brief §8, stretch goal 1.

**Status: built**, after the critical path landed. It was correctly the first thing to cut -- nothing
on the critical path depends on it -- and it was worth building afterwards because it completes the
brief's own sentence: *deterministic replay is how the AI agent invokes it in production*. Discovery
and replay were built; the invocation surface was the missing third.

## Scope

| Module | Responsibility |
|---|---|
| `catalog/store.py` | `CapabilityStore` — list, load, resolve `id@version` |
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

## What was actually built, and what changed from this spec

| Planned | Built | Why |
|---|---|---|
| `catalog/store.py` | ✅ | Plus `unreadable`, so a broken file and a typo do not produce the same message |
| `catalog/overlay.py` | ❌ **dropped** | `TenantBinding.apply()` already does this correctly. Building it again would have been a second implementation of a solved problem, and a second place for it to drift. |
| `catalog/toolspec.py` | ✅ | Three lines over `Capability.tool_schema()`, which is the design working rather than a shortcut |
| `cli/catalog.py` | ✅ as `cua catalog list \| show \| invoke` | Lives in `cli/main.py` with the other commands |
| calling-agent demo | ✅ `cli/agent_demo.py`, `cua agent-demo`, and demo stage 12 | |

**One design decision worth recording.** A capability whose file has been edited on disk is still
*listed*, marked `TAMPERED`, and refused at `load()`. The first implementation swallowed the parse
error and the capability simply vanished from the listing -- which is the wrong failure: an operator
whose artifact was modified needs to be told exactly that, not told it does not exist. `list()` now
parses without hash verification so the entry appears; `load()` verifies, so nothing unverified ever
runs.

## Exit criteria

- [x] `cua catalog list` shows discovered capabilities with their typed signatures
- [x] `cua catalog invoke <id> --input member_id=12345` runs a deterministic replay
- [x] A calling-agent demo invokes a capability by name and handles all four run statuses
- [x] A tampered artifact is refused rather than served

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
