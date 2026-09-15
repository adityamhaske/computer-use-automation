# Design — error taxonomy and the result contract

> Decision rationale lives in [ADR 0003](../adr/0003-four-class-observation-taxonomy.md).

## Two families

**What we observed** (a classification of a moment):

```python
ObservationClass = EXPECTED | BUSINESS_OUTCOME | RECOVERABLE | UNEXPECTED_STATE
```

**How the run ended** (a terminal status):

```python
RunStatus = SUCCESS | BUSINESS_OUTCOME | NEEDS_HUMAN | FAILED
```

They are not the same axis, which is why they are not the same type. `RECOVERABLE` is a thing you
see, never a way a run ends.

## The decision procedure

At every observation point the executor asks, in this order:

```
1. Does the state satisfy the step precondition / checkpoint?   → EXPECTED, proceed
2. Does it match a declared `outcomes` detector?                → BUSINESS_OUTCOME, return to caller
3. Does it match a declared `recovery` detector?                → RECOVERABLE, remediate (bounded)
5. Otherwise                                                    → UNEXPECTED_STATE, FAIL CLOSED
```

Step 5 is the important one. The absence of a match is itself information: this capability does not
understand this screen. The executor stops rather than continuing on the assumption that the click
probably worked.

## Result contract

```python
SUCCESS          -> { outputs, steps, timings, drift_score, strategy_mix, evidence_ref }
BUSINESS_OUTCOME -> { outcome: { code, data }, evidence_ref }
NEEDS_HUMAN      -> { intervention_id, reason, step_id, snapshot_ref, evidence_ref }
FAILED           -> { error: { code, step_id, expected, observed, resolution_debug }, evidence_ref }
```

`BUSINESS_OUTCOME` is a **success of the system** even when it is a negative answer to the user.
`cua replay` exits 0 for it. Conflating it with `FAILED` is the mistake the brief singles out: it
turns a routine "no such member" into a page at 2am, and — worse — trains everyone to ignore the
alert that also fires for real defects.

`NEEDS_HUMAN` is terminal and first-class. Escalation is a normal outcome, not an exception path.

## Failure codes

| Code | Means | Typical cause |
|---|---|---|
| `TARGET_NOT_FOUND` | No candidate matched at any ladder rung | The screen isn't what we expect |
| `TARGET_AMBIGUOUS` | Multiple candidates; resolver refused | Genuinely ambiguous UI, or too-loose descriptor |
| `PRECONDITION_FAILED` | Step's entry condition unmet | Prior step didn't land |
| `CHECKPOINT_FAILED` | Flow completed but success condition false | The happy path wasn't actually reached |
| `ACTION_FAILED` | The driver couldn't perform the action | Control disabled, obscured, detached |
| `TIMEOUT` | Wait condition never satisfied | Slow load, or a condition that can't be true |
| `NAVIGATION_BLOCKED` | Allowlist violation | Redirect off-domain; possible injection |
| `POLICY_DENIED` | Policy refused the action | Irreversible action without approval |
| `SURFACE_UNAVAILABLE` | Driver/session died | Browser crash |
| `RECOVERY_EXHAUSTED` | A recoverable condition outlived `max_attempts` | Persistent, not transient |
| `INPUT_VALIDATION_FAILED` | Caller's inputs failed the declared schema | Bad agent call — caught before touching the UI |
| `UNEXPECTED_STATE` | Matched nothing declared | Fail-closed default |
| `LEASE_LOST` | Dispatch with a stale lease epoch | Automation tried to act after a human took over |
| `INTERNAL` | Our defect | Bug |

Every `FAILED` result carries **step, expected, observed** — the brief's requirement that a failure
be debuggable without reproducing it.

## Recovery is bounded by construction

A recovery rule is declared in the artifact with a detector, a remedy, `max_attempts`, and an
optional backoff. It cannot be open-ended and it cannot be invented at runtime. Exceeding
`max_attempts` converts to `RECOVERY_EXHAUSTED`.

This matters because unbounded recovery is how automations turn a transient 503 into a thousand
retries against a struggling core banking system.

## The fault matrix

`tests/integration/test_fault_matrix.py` asserts one row per injected condition:

| Injected fault | Expected `RunStatus` | Expected code / outcome |
|---|---|---|
| `member_id` with no record | `BUSINESS_OUTCOME` | `member_not_found` |
| `transient_load` (502 once) | `SUCCESS` | recovered after retry |
| `transient_load` (502 always) | `FAILED` | `RECOVERY_EXHAUSTED` |
| `session_timeout` | `SUCCESS` or `NEEDS_HUMAN` | re-auth recovery, else escalate |
| undeclared interstitial dialog | `NEEDS_HUMAN` | `UNEXPECTED_STATE` |
| `validation_error` on submit | `BUSINESS_OUTCOME` | `validation_rejected` |

Every row above is driven by a committed capability and asserted in
`tests/integration/test_fault_matrix.py`. `validation_rejected` was the last one that was not:
the savings-balance lookup submits nothing, so nothing could be rejected. It is covered by
`corebank.member.open_subaccount`, a multi-field form with a confirmation step — the brief's
second example goal — because a taxonomy row with no capability behind it is a claim rather
than a guarantee.
| permission-denied account | `BUSINESS_OUTCOME` | `permission_denied` |
| off-allowlist redirect | `FAILED` | `NAVIGATION_BLOCKED` |

This table is the taxonomy's test. If a row can't be written for a new condition, the condition
hasn't been classified yet.

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
