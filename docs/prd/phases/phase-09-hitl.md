# Phase 09 — HITL escalation & live control transfer ⭐⭐

**Objective.** When the system stops, a human takes control of **the same live session**, acts, and
hands it back — policed and recorded throughout.

Reference: [design/control-transfer.md](../../design/control-transfer.md),
[ADR 0004](../../adr/0004-single-policy-chokepoint.md).

## Build order is deliberate

1. **Lease + epochs** — who may act, right now
2. **Policed `raw_input` path** — human actions through the same chokepoint
3. **Intervention queue + context** — routing with enough to act on
4. **Re-anchor / reconcile resume** — the part most submissions miss
5. **CDP screencast console** — **last**, because it is the largest completion risk

Steps 1–4 are the graded model and are provable headless. Step 5 is pixels. If time runs out, the
fallback is headful Chromium with a claim/release API — still the same session, still policed.

## Scope

| Module | Responsibility |
|---|---|
| `hitl/lease.py` | `Lease{holder, epoch, expires_at}`; stale epoch ⇒ `LEASE_LOST` |
| `hitl/broker.py` | Owns the live session; arbitrates control; state machine |
| `hitl/intervention.py` | Request with capability/goal/step/reason/redacted snapshot + screenshot |
| `hitl/reanchor.py` | Snapshot diff → `human_delta`; skip-forward to first unsatisfied precondition |
| `hitl/console/` | FastAPI + WebSocket; screencast out, `raw_input` in |

## The two things that must be true

- **A stale-epoch dispatch is rejected.** Escalation happens while a step is in flight; without the
  epoch check, that step lands after the human has taken over.
- **Human input cannot bypass policy.** The console submits actions; it does not inject them.

## Exit criteria

- [x] Automation pauses → human drives the **same** session → releases → run re-anchors → completes
- [x] Human actions in evidence, tagged `actor=HUMAN`, in the same shape as automation actions
- [x] Dispatch at a stale epoch ⇒ `LEASE_LOST`
- [x] Human off-allowlist navigation blocked
- [x] Irreversible human action requires explicit confirmation, and is recorded
- [x] Post-handoff state the capability cannot act from ⇒ fails closed
- [x] `human_delta` recorded on release
- [x] A real replay escalation opens a real intervention and pauses the lease
- [x] Operator console built (last, as planned)

### Verification record

218 tests green; `mypy --strict` on 74 files; 5/5 contracts. Build order was as planned: lease,
policed input, intervention queue and re-anchoring first — all provable headless — with the
pixel-streaming console added last.

**A real constraint the console exposed.** Synchronous Playwright is bound to the thread that
*created* it, and a web server handles requests on a threadpool. So the obvious console — a FastAPI
handler calling `driver.observe()` — dies with `greenlet.error: Cannot switch to a different
thread`. This is a property of any co-browsing console, not of this test: automation owns the
browser on one thread and an operator arrives on another.

`hitl/session_thread.py` is the answer: one thread owns the session and everyone else submits
callables. Note the sharper requirement it documents — **the session must be created on that thread,
not merely called from it.** Marshalling to a driver constructed elsewhere just forwards to the
wrong thread, which cost a debugging cycle to discover. It also gives the session the same
single-writer discipline the lease gives it logically.

**The third chokepoint violation caught by the AST scan rather than import-linter:** `cli/main.py`
imported the driver to boot the console session. Moved to `runtime/wiring.build_supervised_session`.
That is three for three where the fix was to move the module rather than add an exemption, and the
reasoning has not changed: each exemption is defensible alone, and together they turn a rule you can
check into a rule you have to argue about.

**`reconcile(from_index=...)` is required, with no default.** A default of 0 is a footgun: once a
flow has advanced, the early steps' preconditions no longer hold — the search box is not on the
member record — so a scan from zero reports the session as unrecognizable when it is simply further
along. The caller knows where it escalated and has to say so.

## Risks

| Risk | Mitigation |
|---|---|
| **Screencast + input injection is the fiddliest build in the project** | Build 1–4 first and prove headless. Pixels last. Documented fallback above. |
| `raw_input` is awkward to authorize semantically | Police it on coarse attributes (scope, resulting navigation). Stated as a limit rather than papered over. |
| Re-anchor skips a step it shouldn't | Skip only when a precondition is *positively satisfied*; ambiguity fails closed. |

---

**Superseded where it differs from what shipped.** This is the phase *plan*; [`docs/design/control-transfer.md`](../../design/control-transfer.md) describes the built system. The notable difference: continuous CDP screencast streaming was cut, and resume is executed by `ReplayExecutor.resume()` rather than left as a computed plan.

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
