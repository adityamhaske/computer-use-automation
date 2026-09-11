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

- [ ] Automation pauses → human drives the **same** session → releases → run re-anchors → completes
- [ ] Human actions in evidence, tagged `actor=HUMAN`, in the same shape as automation actions
- [ ] Dispatch with `epoch-1` ⇒ `LEASE_LOST`
- [ ] Human off-allowlist navigation blocked
- [ ] Irreversible human action requires explicit confirmation, and is recorded
- [ ] Post-handoff state matching no step's precondition ⇒ `UNEXPECTED_STATE`, fails closed
- [ ] `human_delta` written to `evidence/escalation/<run_id>/`

## Risks

| Risk | Mitigation |
|---|---|
| **Screencast + input injection is the fiddliest build in the project** | Build 1–4 first and prove headless. Pixels last. Documented fallback above. |
| `raw_input` is awkward to authorize semantically | Police it on coarse attributes (scope, resulting navigation). Stated as a limit rather than papered over. |
| Re-anchor skips a step it shouldn't | Skip only when a precondition is *positively satisfied*; ambiguity fails closed. |
