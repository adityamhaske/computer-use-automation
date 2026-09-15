# ADR 0003 — Four-class observation taxonomy, separate from run status

- **Status:** Accepted
- **Date:** 2026-09-11
- **Context:** Brief §3.3 and the glossary, which names conflating business outcomes with failures
  "the most common design mistake here"

## Context

A replay observes a screen and must decide what it means. The brief asks us to distinguish three
things: expected business outcomes the caller needs, recoverable conditions, and hard failures.

Working through the mock app's real conditions exposed a fourth case that the three-way split has
nowhere to put:

> The replay lands on a screen that matches **none** of the capability's declared preconditions,
> checkpoints, outcomes, or recovery rules.

This is not a business outcome (nothing declared it), not recoverable (no declared remedy), and not
obviously a hard failure in the sense of a defect — it is simply *a state this capability does not
understand*. A system without this category has to force it into one of the other three, and the
tempting choice is "keep going and see." That is precisely the behaviour that causes incidents in a
bank.

A second confusion: "recoverable" is not a way for a *run* to end. It is a classification of a
*moment*. Mixing it into the terminal status makes the result contract incoherent.

## Decision

**Two distinct type families.**

*What we observed at a point in time:*

```python
ObservationClass = EXPECTED | BUSINESS_OUTCOME | RECOVERABLE | UNEXPECTED_STATE
```

*How the run terminated:*

```python
RunStatus = SUCCESS | BUSINESS_OUTCOME | NEEDS_HUMAN | FAILED
```

Rules connecting them:

- `RECOVERABLE` is **never terminal**. It triggers a bounded, *declared* remedy with `max_attempts`.
  Exhausting attempts converts it to `RECOVERY_EXHAUSTED`, a `FailureCode`. Recovery can never loop.
  Note this is a *failure code*, not an observation class: a hard failure is expressed as
  `RunStatus.FAILED` plus a `FailureCode`, which is why the taxonomy has four classes and not five.
- `UNEXPECTED_STATE` **fails closed**: per `escalation.policy` it becomes `NEEDS_HUMAN` (default) or
  `FAILED(UNEXPECTED_STATE)`. It never becomes "continue."
- `TARGET_AMBIGUOUS` follows the same rule: the resolver refuses rather than picking a candidate.
- `NEEDS_HUMAN` is a **first-class terminal status**, not an exception. Escalation is a normal way
  for a run to end, which is what makes the human-in-the-loop path architectural rather than bolted
  on.

## Consequences

**Good.** The caller's contract is honest: `BUSINESS_OUTCOME(member_not_found)` exits 0 and returns
data, because it is an answer. A `FAILED` carries step, expected, observed, and resolution debug —
enough to fix without reproducing. The fault matrix in `tests/integration/test_fault_matrix.py`
asserts one row per condition, so the taxonomy is tested rather than asserted.

**Bad.** Authoring a capability now requires declaring outcomes and recovery rules explicitly, which
is more work than letting the executor improvise. That is the point: the improvisation is what we
are trying to eliminate. The artifact compiler scaffolds both from the discovery run for a human to
review, so the cost falls mostly on review rather than authoring.

**Limit.** `UNEXPECTED_STATE` is a function of what the capability declares, not of what is
*actually* wrong. A capability with sloppy detectors will over-report it. That is the correct
failure direction — noisy and safe beats silent and wrong — but it does mean the metric to watch is
`unexpected_state_rate` per capability, which `CapabilityEvaluation` records.

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
