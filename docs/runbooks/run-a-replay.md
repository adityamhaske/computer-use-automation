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

```bash
cua replay <artifact> --input member_id=99999           # BUSINESS_OUTCOME(member_not_found)
cua replay <artifact> --fault transient_load            # RECOVERABLE -> recovered -> SUCCESS
cua replay <artifact> --fault transient_load --always   # RECOVERY_EXHAUSTED
cua replay <artifact> --fault undeclared_dialog         # UNEXPECTED_STATE -> fails closed
cua replay <artifact> --fault session_timeout           # re-auth recovery, else escalate
```

## Debugging a failure

`evidence/replay/<run_id>/run_record.json` carries, per step: the resolution strategy that won, how
many candidates were considered, the ambiguity score, the fingerprint comparison, and expected vs
observed at each assertion.

| Failure | Usual cause |
|---|---|
| `TARGET_NOT_FOUND` | The screen isn't what the artifact expects — check the prior step landed |
| `TARGET_AMBIGUOUS` | Descriptor too loose, or the UI genuinely has two matches |
| `CHECKPOINT_FAILED` | Steps ran but the goal state wasn't reached — often a silent validation error |
| `UNEXPECTED_STATE` | The capability doesn't declare this screen. Add an outcome or recovery rule. |
| `RECOVERY_EXHAUSTED` | The "transient" condition wasn't transient |

## Determinism

Same artifact + same inputs + same app state ⇒ identical decision trace. If two replays diverge,
that is a **bug in this system**, not flakiness — `invariants/test_determinism.py` exists to catch it.

Rising `drift_score` across runs means the UI is moving under the artifact. That is the window in
which a re-record or a tenant overlay is cheap.

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
