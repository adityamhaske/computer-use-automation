# ADR 0004 — A single, type-enforced policy chokepoint for every actor

- **Status:** Accepted
- **Date:** 2026-09-11
- **Context:** Brief §3.4 (allowlist, risky/irreversible actions) and §3.6 (human takes control)

## Context

Three different things want to act on the live session: the discovery agent, the replay executor,
and — during an escalation — a human operator. Safety requirements (allowlist, risk tiers,
redaction, audit) apply to all three, but it is easy to end up with the guardrail wired into only
one path.

The concrete trap, which an earlier draft of this design fell into: the operator console streams the
page to a human and forwards their clicks back via CDP `Input.dispatchMouseEvent` directly to the
browser. It works, it feels natural — and it means that the instant a human takes over, the
allowlist, the risk classification, and the evidence trail all stop applying. In a system handling
regulated financial data, escalation would have been a hole in the security model rather than a
feature of it.

## Decision

**One path to a surface, for every actor:**

```
Action ──► PolicyEngine ──► TargetResolver ──► SurfaceDriver
       (authorize)        (resolve or refuse)   (dispatch)
```

Enforced three independent ways, because an invariant everyone agrees with is weaker than one nobody
can violate:

1. **Import rule.** `cua.runtime.dispatcher` is the only module allowed to import `cua.surfaces`
   (`.importlinter` contract `policy-chokepoint`).
2. **Type rule.** `SurfaceDriver.dispatch()` accepts only an `AuthorizedAction`, which carries a
   token instance held exclusively by `PolicyEngine`. A hand-constructed action is rejected at
   runtime.
3. **Reconciliation test.** Every `dispatch` event in a run record must have a matching `authorize`
   event with the same action id.

**Human input is policed, not injected.** The console submits `raw_input` actions — a first-class
member of the action space — which traverse the same path under a distinct **`HUMAN` policy
profile**:

| | AUTOMATION | HUMAN (during intervention) |
|---|---|---|
| Domain allowlist | enforced | **enforced** |
| `safe` actions | allow | allow |
| `elevated` actions | allow if declared | allow, recorded |
| `irreversible` actions | block → escalate | allow **with explicit console confirmation**, recorded |
| Off-artifact navigation | `NAVIGATION_BLOCKED` | allowed within allowlist, recorded |
| Evidence redaction | applied | **applied** |

Escalation therefore *widens authority deliberately and auditably*. It does not disable the
chokepoint.

## Consequences

**Good.** Discovery, replay, and human intervention are the same code path, differing only in who
originates the action — so the guardrails we test are the guardrails that run. "Record what the
human did" comes free and arrives in the same shape as everything else, tagged `actor=HUMAN`.

**Bad.** `raw_input` is genuinely awkward to police semantically: a mouse-move is not a meaningful
unit of authorization. We evaluate it on coarse attributes (target scope, resulting navigation)
rather than pretending otherwise, and say so as a stated limit in REPORT.md §6.

**Cost.** One extra indirection on every action, and the console is harder to build than direct
injection. Both are worth it; the second is the reason Phase 09 builds the control model headless
first and adds pixel streaming last.
