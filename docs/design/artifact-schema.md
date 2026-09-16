# Design — the capability artifact

> Decision rationale lives in [ADR 0002](../adr/0002-immutable-content-addressed-capability.md).
> This document is the reference for what the schema *is*.

A capability artifact is the unit of reuse in this system. It is what the LLM's discovery run
compiles down to, what a human reviews and approves, and what an AI agent invokes in production.

It has to serve three readers at once, which is what makes the design non-obvious:

| Reader | Needs |
|---|---|
| The **replay engine** | Unambiguous, executable steps with explicit success and failure conditions |
| A **human reviewer** | To understand what this does, what it touches, and what could go wrong — without running it |
| A **calling AI agent** | A typed contract: what to pass, what comes back, what can happen |

## Document family

Only the first is the capability. The rest exist so the capability can stay immutable.

```
cua.capability/v1             frozen, content-hashed — the contract
cua.tenant_binding/v1         per-tenant vars + step overrides (base ⊕ overlay)
cua.run_record/v1             one execution: trace, resolutions, drift, evidence refs
cua.capability_evaluation/v1  aggregate over runs: stability, strategy mix, drift trend
cua.capability_approval/v1    draft → approved, approver, scope
```

## Anatomy

### Identity

```yaml
capability:
  id: "corebank.member.savings_balance"   # <vendor-product>.<domain>.<operation>
  version: "1.2.0"
  content_hash: "sha256:…"
```

`id` is namespaced by **vendor product, never tenant** — the precondition for cross-tenant reuse
(ADR 0005). `version` is semver: a step change is minor, a contract change (inputs/outputs) is major.
`content_hash` is recomputed on load and verified, so a run record can name exactly what executed.

### Surface requirements

```yaml
surface:
  kind: web                                          # web | legacy_web | desktop
  driver_capabilities: [semantic_tree, screenshot, raw_input]
  app: { vendor: acme-core, product: MemberDesk, version_range: ">=4.2 <5" }
```

Note what is *absent*: the name of an automation library. The artifact declares **what a driver must
be able to do**, not who does it. That is what lets a UIA driver claim the same artifact.

### Typed contract

```yaml
inputs:
  - { name: member_id, type: string, required: true, pattern: "^[0-9]{4,10}$",
      sensitive: false, example: "12345" }
outputs:
  - { name: savings_balance, type: money, source: { step: read_balance } }
```

These emit JSON Schema directly, so **the artifact is the agent tool contract** — there is no second
source of truth to drift. `sensitive: true` propagates everywhere: the value is masked in logs,
excluded from evidence, blurred in screenshots, and stripped from outbound LLM prompts.

### Steps

<!-- validates: Step -->
```yaml
id: enter_member_id
description: Type the member number into the search field.
action:
  type: type
  value: {$input: member_id}
  target:                       # the target lives on the ACTION, not the step
    role: textbox
    name: {value: Member Number, match: exact}
    scope: {frame: content}
    anchor: {relation: row_of, text: Member Number}
    hints: {css: 'input[name="memno"]'}   # unverified cache — never trusted alone
    recorded_strategy: semantic_exact     # drift baseline
precondition:
  assert: node_exists
  query: {role: textbox, name: Member Number, scope: {frame: content}}
wait: {for: snapshot_stable, timeout_ms: 3000}
postcondition:
  assert: node_has_value
  query: {role: textbox, name: Member Number}
  value: {$input: member_id}
risk: safe                      # safe | elevated | irreversible
```

Every step asserts both before and after. A step that only acts is a step that assumes the click
worked, and assuming is what this system exists not to do.

### Success, outcomes, recovery

Three separate concepts, deliberately (ADR 0003):

<!-- validates: Predicate -->
```yaml
# checkpoint — did we actually reach the state we wanted?
assert: all_of
of:
  - assert: node_exists
    query: {role: cell, name_contains: Savings Balance}
  - assert: node_exists
    query: {role: cell, name_matches: '^\$[0-9,]+\.[0-9]{2}$'}
```

Outcomes are legitimate answers the caller needs, not failures. Recovery is bounded, declared
remediation — never open-ended. Both are top-level keys, separate from the checkpoint and from each
other (ADR 0003):

<!-- validates: Outcome -->
```yaml
code: member_not_found
description: No member exists with that number.
detect: {assert: text_present, value: No records found}
returns: {member_id: {$input: member_id}}
```

<!-- validates: RecoveryRule -->
```yaml
id: transient_load
description: The app server returned a gateway error. Re-fetch and continue.
detect: {assert: http_status_in, codes: [502, 503, 504]}
remedy:
  - {type: reload}
max_attempts: 3
backoff: exponential
scope: any_step
```


### Guardrails and provenance

```yaml
escalation:
  # `triggers`, not `on`: YAML 1.1 resolves a bare `on` key to the boolean True, and an artifact
  # format humans hand-edit should not contain that landmine. (The loader disables the coercion
  # too -- see serde.py -- but the field name should not need the fix.)
  triggers: [target_ambiguous, target_not_found, checkpoint_failed, recovery_exhausted,
             unexpected_state]
  policy: pause_and_request_human      # or: fail_closed

policy:
  allowed_domains: ["{base_url}"]
  allowed_actions: [click, type, select, navigate, extract, press_key]
  max_steps: 40
  max_duration_ms: 120000

provenance:
  discovered_by: { model: "...", run_id: "...", at: "..." }
  transcript_ref: "evidence/discovery/<run_id>/trace.jsonl"
```

`transcript_ref` is a **reference**. §3.2 requires the artifact be decoupled from the raw model
transcript, and inlining it would also make the artifact unreviewable.

## Design notes

**Why declare outcomes at all, rather than detecting failure generically?** Because "no records
found" is indistinguishable from "the page didn't load" to a generic detector, and the two demand
opposite responses. Declaring them makes the capability author state which is which, once, at review
time, instead of the executor guessing at runtime.

**Why a `fingerprint` per target?** It is the drift baseline. On replay we recompute it and compare;
a mismatch or a lower-priority resolution strategy is recorded rather than silently accepted.

**Why is `risk` on the step and not derived?** It *is* also derived — from the action type and the
control's semantics. The explicit annotation is a third signal, because any one of the three can be
fooled (ADR 0004). They are combined conservatively: the highest tier wins.

**What is deliberately not here:** run counts, stability scores, approval state, last-run timestamps,
tenant specifics. All of it lives in the sibling documents above.

---

[Repository](https://github.com/adityamhaske/computer-use-automation) · [Documentation](https://adityamhaske.github.io/computer-use-automation/) · [Design write-up](https://adityamhaske.github.io/computer-use-automation/report/)
