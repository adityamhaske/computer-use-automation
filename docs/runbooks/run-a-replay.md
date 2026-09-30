# Runbook — run a replay

Replay is the **deterministic** half: the production execution path. No model, no decisions, no
surprises.

```bash
make app
cua replay evidence/capabilities/corebank.member.savings_balance@1.0.0.yaml \
  --input member_id=67890
```

## Exit codes and what they mean

| Status | Exit | Meaning |
|---|---|---|
| `SUCCESS` | 0 | Checkpoint verified, typed outputs returned |
| `BUSINESS_OUTCOME` | 0 | A legitimate answer — "no such member" is **not** a failure |
| `NEEDS_HUMAN` | 2 | Stopped safely; an intervention is queued |
| `FAILED` | 1 | A defect. Carries step, expected, observed |

`BUSINESS_OUTCOME` exiting 0 is intentional. Conflating it with failure turns a routine result into a
2am page, and trains everyone to ignore the alert that also fires for real defects.

## Exercising the error paths

Faults are armed on the mock app out of band, never through `cua replay` (it has no fault flag):
`POST /_control/arm` with `{"fault": <name>, "count": <n>}` (`-1` = every request), after signing
in. `cua console --capability <ref> --arm-fault <name>` does the same for a supervised run.

| Input / armed fault | Result |
|---|---|
| `--input member_id=99999` | `BUSINESS_OUTCOME(member_not_found)` |
| `transient_load`, count 1 | `RECOVERABLE` -> recovered -> `SUCCESS` |
| `transient_load`, count -1 | `RECOVERY_EXHAUSTED` -> escalates (`NEEDS_HUMAN`) |
| `undeclared_dialog` | `UNEXPECTED_STATE` -> fails closed |
| `session_timeout` | re-authenticated by `run_capability corebank.auth.sign_on` -> `SUCCESS`; escalates if that cannot run |

The `session_timeout` remedy needs `corebank.auth.sign_on` in the catalog
(`tests/fixtures/capabilities/sign_on.yaml`) and the operator credentials as secrets:
`CUA_SECRET_COREBANK_OPERATOR_ID` and `CUA_SECRET_COREBANK_OPERATOR_PASSWORD`. Without either it
fails closed as `RECOVERY_EXHAUSTED`.

## Debugging a failure

`evidence/replay/<run_id>/run_record.json` carries, per step: the resolution strategy that won and
how many candidates were considered. A failure's `error` carries the step, expected and observed,
and for a targeting failure the rungs tried, the candidates seen and their ambiguity.

| Failure | Usual cause |
|---|---|
| `TARGET_NOT_FOUND` | The screen isn't what the artifact expects — check the prior step landed |
| `TARGET_AMBIGUOUS` | Descriptor too loose, or the UI genuinely has two matches |
| `CHECKPOINT_FAILED` | Steps ran but the goal state wasn't reached — often a silent validation error |
| `UNEXPECTED_STATE` | The capability doesn't declare this screen. Add an outcome or recovery rule. |
| `RECOVERY_EXHAUSTED` | The "transient" condition wasn't transient |

## Determinism

Same artifact + same inputs + same app state ⇒ identical decision trace. If two replays diverge,
that is a **bug in this system**, not flakiness —
`integration/test_fault_matrix.py::test_repeated_replays_produce_identical_decisions` exists to
catch it.

Rising `drift_score` across runs means the UI is moving under the artifact. That is the window in
which a re-record or a tenant overlay is cheap.

---

[Repository](https://github.com/adityamhaske/computer-use-automation) · [Documentation](https://adityamhaske.github.io/computer-use-automation/) · [Design write-up](https://adityamhaske.github.io/computer-use-automation/report/)
