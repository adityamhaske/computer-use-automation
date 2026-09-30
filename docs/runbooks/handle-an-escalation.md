# Runbook — handle an escalation

A run stopped because it could not safely proceed. This is a **designed outcome**, not a crash.

## Why it stopped

`NEEDS_HUMAN` comes from one of the declared triggers in the capability's `escalation.triggers`:

| Trigger | Meaning |
|---|---|
| `TARGET_AMBIGUOUS` | Two plausible controls — it refused rather than guessing |
| `TARGET_NOT_FOUND` | The ladder was exhausted |
| `CHECKPOINT_FAILED` | The flow ran but didn't reach the goal state |
| `RECOVERY_EXHAUSTED` | A transient condition wasn't transient |
| `UNEXPECTED_STATE` | A screen this capability doesn't understand — the fail-closed default |
| `POLICY_DENIED` | Policy refused an action (e.g. an irreversible step with no approval) — only if the capability lists it; the default triggers do not |

## Taking control

```bash
cua console          # http://localhost:8812
```

The queue shows each intervention with the capability and goal, the step it stopped on, the reason, a
redacted snapshot and screenshot, and a link to the run evidence.

**Claim** takes the lease. The lease epoch increments, and any in-flight automation dispatch is
refused with `LEASE_LOST` — so a step that was mid-execution cannot land on the page while you work.

You are driving **the same live session**, not a copy.

## What you can and cannot do

Escalation widens your authority deliberately — but it does not disable the guardrails:

| | You, during an intervention |
|---|---|
| Domain allowlist | **Still enforced** |
| Ordinary actions | Allowed, recorded |
| Irreversible actions | Allowed **with explicit confirmation**, recorded |
| Evidence | Everything you do is captured, tagged `actor=HUMAN` |

Your input is *submitted through* the same policy path automation uses. It is not injected into the
page.

## Handing back

**Release** diffs the session state before and after your work, records `human_delta`, and
returns the lease to automation. The console does not restart the run itself; when a caller resumes
it (`ReplayExecutor.resume()`, as `cua demo` does), the executor:

1. Scans forward from the step that escalated
2. **Skips** a step only if its postcondition already holds — so it does not redo your work; a step
   with no postcondition is never skipped
3. Resumes at the first step not visibly done, **provided its precondition holds**
4. Otherwise stops with `UNEXPECTED_STATE`

Step 4 is not a bug. If you left the session somewhere the capability doesn't recognize, resuming
would be guessing.

## Afterwards

A recurring escalation is a capability defect, not an operations problem. The usual fixes:

- Screen seen repeatedly but undeclared → add an `outcomes` or `recovery` entry
- `TARGET_AMBIGUOUS` on the same step → tighten the descriptor (add a scope or an anchor)
- Rising `drift_score` for one tenant → add a `TenantBinding` overlay, or re-record

---

[Repository](https://github.com/adityamhaske/computer-use-automation) · [Documentation](https://adityamhaske.github.io/computer-use-automation/) · [Design write-up](https://adityamhaske.github.io/computer-use-automation/report/)
