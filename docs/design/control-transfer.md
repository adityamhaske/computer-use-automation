# Design — control transfer (human-in-the-loop)

> Decision rationale lives in [ADR 0004](../adr/0004-single-policy-chokepoint.md).

The brief asks for something specific and easy to fake: a human must take control of **the same
live session** the automation was using — not a fresh one — do the manual work, and hand control
back so the run can finish. This document describes the mechanism.

## State machine

```
RUNNING ──escalate──► PAUSED ──human_claim──► HUMAN_CONTROL
   ▲                  (epoch++)               (epoch++)
   │                                                │
   └── resume (epoch++) ◄── RESUMING ◄── human_release (epoch++)
```

Re-anchoring happens after the lease is back in `RUNNING`: `ReplayExecutor.resume()` reconciles
against the live screen before its first action (below). `cua demo` drives that resume; the
operator console's **Release** returns the lease to automation but does not itself restart the run.

## The lease

```python
Lease(holder: Actor, epoch: int, expires_at: datetime)
```

`holder ∈ {AUTOMATION, HUMAN, SYSTEM}` — `SYSTEM` while the session is `PAUSED` or `RESUMING`, when
nobody may act. Every transition increments `epoch` monotonically.

**Both actors are checked before anything reaches a surface.** An operator's input is refused
by the broker unless `lease.assert_held(HUMAN, epoch)` passes. An automation run checks that it
holds the session when it starts (or resumes), then carries its epoch into every dispatch, and the
dispatcher refuses a stale one with `LEASE_LOST` — every change of holder advances the epoch, so the
two checks together cover every dispatch. A run with no broker has no lease and no operator to race,
and skips both.

This kills a real race rather than a theoretical one. Escalation happens *while* an automation step
is in flight — that is what "stuck" usually means. Without the epoch check, the in-flight step can
land on the page a half-second after the human has taken over and started typing. With it, the stale
dispatch is refused and recorded — as a *refused attempt*, not as a dispatch: nothing reached the
surface, so the reconciliation that proves "nothing reaches a surface without authorization" does not
count it.

**A hold that lapses does not strand the session.** When an operator's hold expires,
`SessionBroker.sweep()` returns the session to `PAUSED` and the intervention to the queue flagged
`ABANDONED` — still listed and still claimable, so the next operator can take the session and can see
that a person had it and stopped. A claim that fails (for example, a `hold_for` that overflows the
clock) leaves no trace: the request goes back exactly as it was. Releasing is validated by the lease
*before* anything is recorded, so a refused second release cannot overwrite the record of what the
first operator did.

## What "stuck" means

Escalation is triggered by declared conditions, not by a vibe:

| Trigger | From |
|---|---|
| `TARGET_AMBIGUOUS` | Resolver refused to guess |
| `TARGET_NOT_FOUND` | Ladder exhausted |
| `CHECKPOINT_FAILED` | Reached the end, didn't reach the state |
| `RECOVERY_EXHAUSTED` | A transient condition wasn't transient |
| `UNEXPECTED_STATE` | Fail-closed default |
| `POLICY_DENIED` | Policy refused an action — e.g. an irreversible step with no approval. Escalates only if listed; the default triggers do not include it |

The capability declares which of these escalate via `escalation.triggers`, and whether the disposition is
`pause_and_request_human` or `fail_closed`.

## The intervention request

Carries what an operator needs to act without archaeology: capability id/version and goal, current
step id, the reason it stopped, a redacted `UiSnapshot`, a redacted screenshot, and the run's
evidence reference.

## Taking control — policed, not injected

The console does **not** forward CDP input to the page. It submits `raw_input` actions to the
`SessionBroker`, which runs them through the same
`Action → TargetResolver → PolicyEngine → SurfaceDriver` path under the `HUMAN` policy profile
(table in ADR 0004).

Consequences worth stating plainly:

- A human still cannot navigate off the allowlist.
- An irreversible action still requires explicit confirmation — but a human *can* confirm it, which
  is the entire point of escalating.
- Every human action lands in the same evidence stream, in the same shape, tagged `actor=HUMAN`.
  "Record what the human did" is not a feature we built; it is a consequence of the path.

## Handing control back: re-anchor and reconcile

This is the part that is easy to get wrong. The human has been operating the UI. The state has moved.
Blindly resuming at step *N* would re-run work they already did — at best redundant, at worst a
double submission.

On `human_release` the broker **snapshot-diffs** pre-handoff against post-handoff and records the
delta as `human_delta`. When the run is then resumed, `ReplayExecutor.resume()` scans forward from
the step that escalated (`hitl/reanchor.py`):

1. A step whose **postcondition already holds** is treated as done and skipped — so work the human
   completed is not repeated. A step with no postcondition is never skipped.
2. The first step not visibly done is where the run resumes — **provided its precondition holds**.
3. If it does not, that is `UNEXPECTED_STATE` and the run **fails closed** rather than picking the
   nearest step.

Step 3 is the fail-closed rule applied to the handoff seam, and it matters: "the human left the
session somewhere I don't recognize" is exactly the moment not to improvise.

## What is mocked, and why

The operator console is **single-operator, no-auth, local**. Multi-operator routing, SSO, queue
assignment, and supervisor sign-off are not built.

What is *real*: the lease and its epochs, the policed input path, the evidence trail, the
re-anchoring resume, and the fact that the human drives the same browser session the automation was
using. The scope note in the brief asks for "a minimal but real handoff… plus a clear design for the
rest" — the control-transfer model is the part that had to be real, and it is.

**Build order (deliberate), and what shipped.** The lease, the policed `raw_input` path and
re-anchoring were built **first** and proven headless. Continuous CDP screencast streaming was the
last item and is the one that was cut: the console sends a still frame on connect and re-captures it
after every policed gesture, which is enough to see the page and act on it, and is not co-browsing.

Resume is *executed*, not merely computed. `ReplayExecutor.resume()` re-anchors against the live
screen and continues the run on the operator's session — skipping the entrypoint navigation, adopting
the epoch the handoff produced, and carrying forward outputs and spent recovery attempts. For a while
`reconcile()` returned a plan that no caller consumed, which meant the system could pause and cede
control but never finish.

---

[Repository](https://github.com/adityamhaske/computer-use-automation) · [Documentation](https://adityamhaske.github.io/computer-use-automation/) · [Design write-up](https://adityamhaske.github.io/computer-use-automation/report/)
