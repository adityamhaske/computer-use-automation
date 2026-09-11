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
   └── RESUMING ◄── re-anchor + reconcile ◄── human_release (epoch++)
       (epoch++)
```

## The lease

```python
Lease(holder: Actor, epoch: int, expires_at: datetime)
```

`holder ∈ {AUTOMATION, HUMAN, NONE}`. Every transition increments `epoch` monotonically.

**Every `dispatch()` asserts `lease.held_by(actor, epoch)`.** A dispatch carrying a stale epoch is
rejected with `LEASE_LOST`.

This kills a real race rather than a theoretical one. Escalation happens *while* an automation step
is in flight — that is what "stuck" usually means. Without the epoch check, the in-flight step can
land on the page a half-second after the human has taken over and started typing. With it, the stale
dispatch is refused and recorded.

## What "stuck" means

Escalation is triggered by declared conditions, not by a vibe:

| Trigger | From |
|---|---|
| `TARGET_AMBIGUOUS` | Resolver refused to guess |
| `TARGET_NOT_FOUND` | Ladder exhausted |
| `CHECKPOINT_FAILED` | Reached the end, didn't reach the state |
| `RECOVERY_EXHAUSTED` | A transient condition wasn't transient |
| `UNEXPECTED_STATE` | Fail-closed default |
| `risky_action_unapproved` | Policy requires a human for an irreversible action |

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

On `human_release` the executor:

1. **Snapshot-diffs** pre-handoff against post-handoff and records the delta as `human_delta`.
2. **Re-evaluates preconditions** from the current step forward.
3. **Skips forward** to the first step whose precondition is *not* yet satisfied — so work the human
   completed is not repeated.
4. If the state matches **no** step's precondition, that is `UNEXPECTED_STATE` and it **fails
   closed** rather than picking the nearest step.

Step 4 is the fail-closed rule applied to the handoff seam, and it matters: "the human left the
session somewhere I don't recognize" is exactly the moment not to improvise.

## What is mocked, and why

The operator console is **single-operator, no-auth, local**. Multi-operator routing, SSO, queue
assignment, and supervisor sign-off are not built.

What is *real*: the lease and its epochs, the policed input path, the evidence trail, the
re-anchoring resume, and the fact that the human drives the same browser session the automation was
using. The scope note in the brief asks for "a minimal but real handoff… plus a clear design for the
rest" — the control-transfer model is the part that had to be real, and it is.

**Build order (deliberate).** Phase 09 builds the lease, the policed `raw_input` path, and
re-anchoring **first**, and proves them headless. CDP screencast pixel streaming is added **last**.
The control-transfer model is what's graded; the pixels are polish, and they are also the single
largest completion risk in the project. If they are cut, the fallback is headful Chromium with a
claim/release API — still the same live session, still policed, still evidenced.
