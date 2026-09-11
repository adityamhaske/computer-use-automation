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
4. Known hard-failure condition?       → HARD_FAILURE, stop
5. Otherwise                           → UNEXPECTED_STATE, FAIL CLOSED
```

## Exit criteria

- [ ] Replays the Phase 07 artifact with **different** inputs and returns typed outputs
- [ ] **All injected faults classified correctly** — the full fault-matrix table passes
- [ ] `member_not_found` returns `BUSINESS_OUTCOME` and **exits 0**
- [ ] Undeclared interstitial ⇒ `UNEXPECTED_STATE`, run stops
- [ ] Determinism: N replays produce an identical decision trace and identical per-step strategies
- [ ] **`invariants/test_no_llm_in_replay.py` passes** — including with the LLM client patched to raise
- [ ] Every `FAILED` result carries step, expected, observed

## Risks

| Risk | Mitigation |
|---|---|
| Hidden nondeterminism (dict ordering, timing, retries) | Determinism test compares full traces across N runs; scoring is pure; ties break on document order. |
| Waits that mask real failures | Timeouts are declared per step and a timeout is a *result*, not a retry. |
| Recovery loops | `max_attempts` is mandatory in the schema — a recovery rule without one fails validation. |
