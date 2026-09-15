# Phase 08 — Deterministic replay engine ⭐⭐

**Objective.** Execute a capability with no LLM in the loop, classify everything it sees, and return
a structured result.

**Half the grade lives here.** This and Phase 09 are what the graders said submissions most often
fake.

Reference: [design/error-taxonomy.md](../../design/error-taxonomy.md).

## Scope

| Module | Responsibility |
|---|---|
| `replay/executor.py` | The step loop: precondition → resolve → authorize → dispatch → wait → postcondition |
| `replay/wait.py` | Declared wait policies. **No `sleep`** — wait on conditions with timeouts |
| `replay/checkpoint.py` | Assert the success condition; never assume |
| `replay/classify.py` | The five-step decision procedure; `UNEXPECTED_STATE` fail-closed default |
| `replay/recovery.py` | Bounded, declared remediation; `max_attempts` → `RECOVERY_EXHAUSTED` |
| `replay/extract.py` | Typed output extraction |
| `replay/result.py` | `RunResult` assembly + `RunRecord` emission |

## The decision procedure

```
1. Satisfies precondition/checkpoint?  → EXPECTED, proceed
2. Matches a declared outcome?         → BUSINESS_OUTCOME, return to caller (exit 0)
3. Matches a declared recovery rule?   → RECOVERABLE, remediate (bounded)
4. Nothing declared explains it?       → UNEXPECTED_STATE, fail closed
5. Otherwise                           → UNEXPECTED_STATE, FAIL CLOSED
```

## Exit criteria

- [x] Replays with **different** inputs and returns typed outputs
- [x] **All 12 fault-matrix rows pass**
- [x] `member_not_found`, `no_savings_account`, `permission_denied` return `BUSINESS_OUTCOME`, exit 0
- [x] Undeclared interstitial ⇒ fails closed, escalates, reports **no outputs**
- [x] Determinism: repeated replays produce identical outputs and identical per-step strategies
- [x] Replay with the model client patched to raise still succeeds
- [x] Every `FAILED` result carries step, expected, observed
- [x] Recovery bounded; an unclearing transient ⇒ `RECOVERY_EXHAUSTED`

### Verification record

201 tests green; `mypy --strict` on 67 files; 5/5 contracts.

**A refinement to the fail-closed rule.** The design said a state "matching nothing the capability
declares" is `UNEXPECTED_STATE`. Taken literally that is unworkable — a freshly compiled capability
declares no outcomes at all, so every page would be unrecognized and every replay would escalate on
its first step. The rule that actually holds is narrower: fail closed when a *declared expectation*
is violated and nothing declared explains why. A step with no precondition asserts nothing, so there
is nothing to violate. A step whose precondition fails has had an explicit expectation broken, and
if no outcome or recovery rule accounts for it, the run stops. That keeps the guarantee where it
matters without requiring a capability to enumerate every page before it can run.

**Four real bugs, each found by a test that existed for a different reason:**

1. **Parameter substitution never happened.** The executor passed the `{$input: member_id}`
   *reference object* to the driver, which stringified it — and the field's `maxlength=10` truncated
   it to exactly `input_name`. A plausible-looking wrong value rather than a crash. Caught by the
   step's own postcondition two steps before the failure would otherwise have surfaced, which is
   precisely the job a postcondition exists to do. Fixed by `replay/bind.py`, which is also where
   `{$secret}` is resolved — inside the dispatch, so the value never exists anywhere a snapshot,
   trace or screenshot is produced from.
2. **HTTP status was tracked for the main frame only.** On a frameset app the shell loads fine and
   the *content frame* returns the 502, so the declared transient-failure recovery was permanently
   unreachable — on exactly the kind of application this project targets.
3. **`reload` was missing from the global allowed actions.** Every transient-failure remedy was
   silently denied by policy.
4. **A refused remedy looked like one that ran.** `_recover` ignored the dispatch result, so a
   denied remedy burned the whole attempt budget and reported `RECOVERY_EXHAUSTED` for a condition
   nothing had ever tried to clear.

Bugs 3 and 4 are the same incident seen twice, and 4 is the more important one: the system was
reporting a confident, wrong diagnosis. It now reports that the remedy could not run.

## Risks

| Risk | Mitigation |
|---|---|
| Hidden nondeterminism (dict ordering, timing, retries) | Determinism test compares full traces across N runs; scoring is pure; ties break on document order. |
| Waits that mask real failures | Timeouts are declared per step and a timeout is a *result*, not a retry. |
| Recovery loops | `max_attempts` is mandatory in the schema — a recovery rule without one fails validation. |

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
