# Design write-up

> **Discovery is probabilistic. Execution is deterministic.**
> The model is allowed to be uncertain exactly once. That uncertainty is compiled into an artifact
> that never needs it again.

Every claim below is backed by something runnable: an enforced import contract, a test, or a file in
[`evidence/`](evidence/). Where something is not built, it says so under §7 rather than being
implied.

---

## 1. Architecture

An LLM drives a live legacy UI to a goal **once**. That run is compiled into a typed, versioned
**capability artifact**. From then on the artifact is replayed with no model in the decision loop,
and an AI agent invokes it like a typed function.

```
                    Action  (from discovery, replay, or a human operator)
                      │
              ┌───────▼────────┐
              │ TargetResolver │   pure, read-only, over an already-captured snapshot
              └───────┬────────┘
              ┌───────▼────────┐
              │  PolicyEngine  │   the only thing that can mint an AuthorizedAction
              └───────┬────────┘
              ┌───────▼────────┐
              │  SurfaceDriver │   dispatch(AuthorizedAction) -- accepts nothing else
              └───────┬────────┘
              ┌───────▼────────┐
              │   EvidenceBus  │   append-only, redacted, actor-tagged
              └────────────────┘
```

**Discovery, replay, and human intervention are the same path.** They differ only in who originates
the action. That is deliberate: it is the answer to "how do you know your guardrails apply in
production?" — there is one path, so a guardrail cannot be present on one and missing from another.

Resolution runs **before** policy. The pipeline was documented the other way round and implementing
it proved that wrong: risk classification's second signal is *what the control says it does*
("Transfer Funds"), which requires the resolved node. Authorizing first means authorizing
half-blind. Resolution is pure and touches nothing, so running it first is strictly safer, and the
invariant was never "policy runs first" — it is *nothing reaches a surface without authorization*.

Three enforcement mechanisms, because an invariant nobody can violate beats one everybody agrees
with: an **import contract** (only `cua.runtime` may import `cua.surfaces`), a **type guard**
(`dispatch()` takes only an `AuthorizedAction`, mintable only by `PolicyEngine`), and an **AST scan**
over every file. The scan has now caught four violations the import graph missed — including one in
this phase. Each time the fix was to move the module, never to add an exemption: each exemption is
defensible alone, and together they turn a rule you can check into a rule you have to argue about.

## 2. Artifact schema

The focal point. `cua.capability/v1` — Pydantic, serialized as YAML, immutable and content-addressed.
A full compiled example is in [`evidence/capabilities/`](evidence/capabilities/).

Four decisions carry most of the weight:

**Targets are semantic, never selectors.** A target is a `TargetDescriptor` — role, accessible name,
structural anchor, scope, ordinal. A CSS selector may appear only under `hints`, an unverified cache
accepted only if the node it finds *also* satisfies the semantic assertion. `role: button,
name: "Search"` means the same thing in Windows UIA as in ARIA; `input.btn1` does not.

**Business outcomes, recovery, and failure are three distinct top-level keys.** The brief names
conflating the first with the third as the most common design mistake here, so the schema makes them
structurally impossible to conflate rather than trusting the executor to.

**The capability is immutable; everything a *run* produces lives elsewhere** — `RunRecord`,
`CapabilityEvaluation`, `CapabilityApproval`, `TenantBinding`, keyed by `id@version`. A definition
that accumulates telemetry stops being reviewable, and "which version ran?" stops being answerable.
Content-addressing makes that question exact.

**`capability.id` is namespaced by vendor product, not tenant**, and `surface.driver_capabilities`
declares what a driver must *be able to do* (`semantic_tree`, `screenshot`) rather than naming a
library. Both are preconditions for reuse — a tenant-namespaced id cannot be shared, and an artifact
that names Playwright cannot be claimed by a UIA driver.

The compiler emits a **draft**, and says so in the artifact itself: a single discovery run observed
only the happy path, so it declares **no** outcomes and **no** recovery rules, with review notes
naming exactly what a reviewer must add. Inventing detectors a run never observed would produce a
capability whose error handling is fiction. The `draft → approved` gate exists for this.

## 3. Determinism & error handling

Two type families, deliberately separate — *what we observed* and *how the run ended*:

| `ObservationClass` | meaning | leads to |
|---|---|---|
| `EXPECTED` | matches a precondition/checkpoint | proceed |
| `BUSINESS_OUTCOME` | matches a declared outcome | terminate, **exit 0** |
| `RECOVERABLE` | matches a declared recovery rule | bounded remedy, then retry |
| `HARD_FAILURE` | declared-impossible | stop |
| `UNEXPECTED_STATE` | matches **nothing** declared | fail closed |

`RunStatus` is terminal and separate: `SUCCESS | BUSINESS_OUTCOME | NEEDS_HUMAN | FAILED`.

"No such member" **exits 0**. It is a successful execution that returned a negative answer; treating
it as a failure turns a routine result into a 2am page and trains everyone to ignore the alert that
also fires for real defects.

`RECOVERABLE` is explicitly non-terminal and bounded — exceeding `max_attempts` converts it to
`RECOVERY_EXHAUSTED`, a hard failure. Recovery can never loop forever.

**Fail-closed is the default disposition**, with two triggers. An observed state matching nothing
declared is `UNEXPECTED_STATE` and the run stops; it never proceeds on the assumption that the click
probably worked. A target with two surviving candidates is `TARGET_AMBIGUOUS` and the resolver
**refuses** — there is no similarity threshold to tune, because a tunable threshold is a knob that
eventually gets turned. In a bank, guessing is the expensive outcome.

Determinism is enforced, not asserted: `cua.replay` may not import `cua.agent` or any HTTP client
(contract, `allow_indirect_imports = False`); the resolution ladder is a fixed order with pure
scoring and ties broken by document order, never by dict iteration, clock, or random; the `vision`
rung is hard-disabled in replay because pixel coordinates are not reproducible.

**Where a hard failure goes is the artifact's decision, not the executor's.** `escalation.triggers`
and `escalation.policy` decide whether a given failure pauses for a human or terminates. In the
committed artifact `recovery_exhausted` escalates — a 502 that outlives its retry budget is
something an operator can act on — while `input_validation_failed` terminates, because nobody can
fix a malformed argument by taking over the session. Writing the demo, I expected an exhausted
recovery to come back `FAILED` and it came back `NEEDS_HUMAN`; the artifact was right and I was
wrong, which is the point of putting the routing in the artifact.

Four injectable faults exercise the taxonomy end to end in
[`tests/integration/test_fault_matrix.py`](tests/integration/test_fault_matrix.py), and **all four
terminal statuses are present in [`evidence/`](evidence/)** — including the two that are not
successes. `make eval` then measures determinism across repeated replays and reports it as a
boolean per capability, because "94% deterministic" is not a property anyone can act on.

## 4. Heterogeneity & multi-tenant

The seam is `UiSnapshot` — a normalized `{node_id, role, name, value, states, bounds, scope}` tree.
Drivers populate it from whatever their surface offers: a CDP accessibility tree today, Windows UIA
or macOS AX tomorrow. **`cua.targeting` and `cua.perception` may not import Playwright or
`cua.surfaces`** (enforced), so the artifact and the resolver cannot grow web-shaped assumptions.
`surfaces/desktop_uia/` is an interface-only stub documenting the UIA→`UiSnapshot` mapping.

Tenants are an **overlay**, never a fork: `TenantBinding` supplies vars and per-step overrides,
resolved at load into an effective capability whose content hash covers the overlay.

The no-CSS claim is **measured, not asserted**. Variant B is a second tenant that relabels controls
("Member Number" → "Member #", breaking `semantic_exact`) *and* restyles the markup (invalidating
every cached CSS hint) while preserving row structure, so a run can only succeed by descending the
ladder to `structural_anchor`.

**Measured, in [`evidence/evals/cross_tenant.md`](evidence/evals/cross_tenant.md):** every
`hints.css` in the artifact names a selector that does not exist on Variant B, and the runs succeed
anyway — 100% success, zero wrong actions, decisions reproducible. Case A (markup churn) is carried
by `semantic_exact` because the labels survived; case B (rebranding) is carried by
`structural_anchor` plus the overlay because they did not. Both rungs are load-bearing, which is why
the ladder has both.

Building that suite also **falsified the reuse claim as it then stood** — see §7. The overlay could
retarget a control but not restate the assertions around it, so every rebranded run failed closed on
a precondition written in the recorded bank's vocabulary. Fixed, and the fix is what makes the
number above meaningful.

A spike in Phase 01 **disproved my own design claim** here, which is why it was run early:
`structural_anchor` does *not* survive rebranding, because the anchor text *is* the label cell and
gets relabeled too. Variant B was reshaped into two separate cases — markup churn (structure holds,
hints die) and rebranding (names change) — and the design doc corrected rather than quietly
rewritten. The honest claim is narrower than the one I started with: structural anchoring survives
*markup* change, not *vocabulary* change. Vocabulary change is what the tenant overlay is for.

## 5. Escalation & handoff

`NEEDS_HUMAN` is a **first-class terminal status**, not an exception. That is what makes escalation
architectural rather than bolted on.

The operator works the **same live session**, and the handoff is guarded by a `Lease{holder, epoch}`
with monotonic epochs. Every dispatch asserts the lease; a dispatch carrying a stale epoch is
rejected with `LEASE_LOST`. This kills the real race: an in-flight automation step acting *after* a
human has taken over.

**Human input does not bypass policy.** The console never injects events into the page. It submits
`raw_input` actions to the broker, which runs them through the same
resolve → authorize → dispatch → evidence path under a **`HUMAN` policy profile**. Escalation
*widens authority deliberately and auditably* — a human may perform an irreversible action with
explicit confirmation, where automation is blocked and escalates — but the domain allowlist and
redaction apply unchanged.

**Resume re-anchors.** The operator may have advanced the state, so the executor snapshot-diffs
across the handoff, records a `human_delta`, and skips forward to the first step whose precondition
is not yet satisfied rather than blindly re-running work the human already did. If the post-handoff
state matches no step, that is `UNEXPECTED_STATE` and it fails closed — the same rule as everywhere
else.

This is visible in the committed evidence rather than described: the escalation run record shows
automation acting at epoch 1, a human claiming the lease and acting twice at epoch 3, lease
transitions `claimed → released → resumed`, and **zero unauthorized dispatches**.

## 6. Safety

One chokepoint, three enforcements (§1), applied identically to all three actors.

**Allowlist** — domains, URL patterns, action types, step and duration caps, in
[`config/policy.yaml`](config/policy.yaml). Navigation outside it is `NAVIGATION_BLOCKED`, for humans
too. A capability's own `allowed_domains` **narrows** the global list; a bug where a narrowed
capability still reached anything the global patterns matched was found and fixed in Phase 05.

**Risk tiers** — `safe → elevated → irreversible`, classified from three independent signals (action
type × target semantics via a danger lexicon × explicit artifact annotation), because any one alone
is fooled. Automation is blocked from irreversible actions and escalates. Replay requires an approved
capability **and** an explicit caller opt-in — two independent gates, because either alone is one
accident away from a wire transfer.

**Redaction at every sink, including outbound LLM prompts.** Regulated data should not leave the
process because a model asked for context. Secrets are `{$secret: ref}`, resolved at dispatch, and
registered with the redactor by literal — no pattern can recognise an arbitrary password.

Over-redaction is also a failure, which this phase demonstrated: `id@version` matched the email
pattern, so every run record read `<redacted:email>` in the one field naming which capability ran —
the exact question content-addressing exists to answer. Now tested in both directions.

**Prompt injection** is scoped, not solved: page text is untrusted input, and the defenses are a
closed action space, policy evaluated *outside* the model, and no authority for the model to widen
the allowlist or approve its own risky action. A model that is talked into a *permitted* action on a
*permitted* target still performs it. Residual risk, stated rather than claimed away.

No real credentials or PII exist anywhere in this repository; the mock app is seeded with fake data.

## 7. Cuts

Both declared stretch goals were built after the critical path landed, so this section is shorter
than it was. What each one changed is worth stating, because neither was free:

- **Evals** (`cua eval`) turned §4's central claim from an assertion into a measurement — and
  immediately falsified part of it. Replaying end to end on the rebranded tenant showed that a
  tenant overlay could retarget a control but **could not restate the step's precondition or the
  capability's checkpoint**, both of which assert the recorded institution's vocabulary. Every
  Variant B run failed closed on an assertion written for a different bank. The earlier test had
  only checked that the retargeted control *resolved*, which it did. `StepOverride` now carries
  `precondition`/`postcondition` and `TenantBinding` carries `checkpoint`; cross-tenant replay is
  100% with zero wrong actions. The claim is now true, and it was not before.
- **Capability catalog** (`cua catalog`, `cua agent-demo`) completes the brief's own sentence —
  "deterministic replay is how the AI agent invokes it in production". The store verifies content
  hashes on load and refuses a tampered artifact, while still *listing* it as `TAMPERED`: an
  operator whose artifact was edited needs to be told that, not told it does not exist.

Still cut, in the order they would be restored:

- **Operator console pixel streaming** — the lease, the policed `raw_input` path, and re-anchoring
  are built and evidenced; CDP screencast to the browser console is not. The model is what is
  graded; the pixels are polish. Losing them costs the operator's view, not the guarantees.
- **Desktop surface** — interface-only stub, on purpose. Building it would prove the seam; declaring
  it and enforcing the import rule is what the seam is worth here.
- **Multi-operator routing, SSO, audit sign-off** — designed, not built. Single local operator.
- **Assisted LLM fallback on replay failure** — would breach the no-LLM-in-replay invariant without
  a separate, explicitly policed path. Not worth the ambiguity.

Two honest weaknesses rather than cuts:

- **The compiler can anchor on data.** In the committed artifact, `read_account_status` anchors
  `adjacent_to` the text `$812.30` — a *value* from the discovery run, not a stable label. It
  replays correctly against the seeded app and would break against different data. The review notes
  warn about lifted parameters but not about this; the `draft → approved` gate is the mitigation,
  and a reviewer would catch it. It should be a compiler rule.
- **`raw_input` is policed coarsely.** Policy evaluates it on target scope and resulting navigation
  rather than pretending a mouse-move is semantic. That is the right call for drag and scroll, but
  it means a human's raw click is authorized with less precision than a semantic action.

---

### What to look at

| | |
|---|---|
| The artifact | [`evidence/capabilities/`](evidence/capabilities/) |
| All four terminal statuses | [`evidence/replay/`](evidence/replay/), [`evidence/escalation/`](evidence/escalation/) |
| The handoff, with actors and epochs | `run_record.json` in [`evidence/escalation/`](evidence/escalation/) |
| The invariants, enforced | [`.importlinter`](.importlinter), [`tests/invariants/`](tests/invariants/) |
| The whole story, one command | `make demo` (12 stages) |
| The numbers behind §3 and §4 | [`evidence/evals/`](evidence/evals/), regenerated by `make eval` |
| An agent calling a capability by name | `cua catalog list`, then `cua agent-demo` |
