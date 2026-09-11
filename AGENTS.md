# AGENTS.md — working agreement for this repository

This file is the contract for anyone (human or AI) writing code here. It exists because this
system's value is almost entirely in a handful of invariants; the code that enforces them is
ordinary, and the code that violates them looks ordinary too.

Read this before your first edit. `CLAUDE.md` points here.

---

## The thesis

> **Discovery is probabilistic. Execution is deterministic.**

An LLM is allowed to be uncertain exactly once — during discovery, when it is figuring out how to
accomplish a goal in an application it has never seen. That uncertainty is then *compiled* into a
typed, versioned capability artifact. From that point on the artifact is executed by a deterministic
engine with no model in the loop.

Every invariant below protects that line. If you find yourself weakening one to make something work,
you are probably about to make the system's core claim untrue. Stop and raise it instead.

---

## The nine invariants

Each is mechanically enforced. The enforcing test is named so you can see it fail on purpose.

### 1. Replay never calls or imports the LLM
`cua.replay` may not import `cua.agent`, `httpx`, or any model client — directly or transitively.

*Why:* it is the whole thesis. A replay that can quietly ask a model for help is not deterministic,
is not cheap, and is not auditable.

*Enforced by:* `.importlinter` contract `no-llm-in-replay`;
`tests/invariants/test_no_llm_in_replay.py` also replays a real artifact with the LLM client
monkeypatched to raise, proving the import rule isn't being routed around at runtime.

### 2. No action reaches a driver without policy authorization
The only path to a surface, for every actor, is:

```
Action ──► TargetResolver ──► PolicyEngine ──► SurfaceDriver
       (resolve or refuse)    (authorize)      (dispatch)
```

`cua.runtime.dispatcher` is the **only** module permitted to call `dispatch()`.
`SurfaceDriver.dispatch()` accepts only an `AuthorizedAction`, which carries a token that only
`PolicyEngine` can mint.

*Note the order.* An earlier version of this document put policy first. Implementing it exposed the
flaw: risk classification's second signal is what the control *says it does*, which requires the
resolved node — so authorizing first means authorizing half-blind. Resolution is a pure function
over an already-captured snapshot; it touches nothing. Running it first means policy decides
knowing exactly what it is about to act on. The invariant was never "policy runs first" — it is
**nothing reaches a surface without authorization**.

*Why:* a guardrail with one bypass is not a guardrail. Discovery, replay, and human intervention all
take the same path, which is how we know production behaves like the tests.

*Enforced by:* `.importlinter` contract `policy-chokepoint`;
`tests/invariants/test_policy_chokepoint.py` (a hand-constructed action is rejected, and every
dispatch event in a run record must have a matching authorization event).

### 3. Target identity is a semantic `TargetDescriptor`, never a raw CSS selector
Artifacts describe controls by role, accessible name, scope, structural anchor, and ordinal.
Surface-specific data (CSS paths, node ids) may appear **only** inside `TargetDescriptor.hints`, and
a hint is an *unverified cache*: it is accepted only if the node it resolves to also satisfies the
semantic assertion.

*Why:* this is the seam that lets the same artifact replay on a Windows UIA driver, and the reason a
rebranded tenant doesn't require a re-record.

*Enforced by:* `.importlinter` contract `surface-neutral-targeting`;
`tests/integration/test_variant_b.py` (Variant B invalidates every cached CSS hint and the run still
succeeds via `structural_anchor`).

### 4. Human input cannot bypass policy or evidence
The operator console never injects into the page. It submits `raw_input` actions to the
`SessionBroker`, which runs them through the same chokepoint under the **`HUMAN` policy profile**.

Escalation *widens* authority deliberately and auditably — a human may do things automation may
not — but the domain allowlist, the risk classification, the redaction, and the evidence bus all
still apply.

*Why:* the moment a person takes over is exactly when a regulated system most needs an audit trail.

*Enforced by:* `tests/invariants/test_human_control_safety.py`.

### 5. Unknown states fail closed
If the observed UI matches no declared precondition, checkpoint, outcome, or recovery rule, it is
`UNEXPECTED_STATE` and the run **stops**. If a target resolves ambiguously, the resolver **refuses**
with `TARGET_AMBIGUOUS`. Never proceed on the assumption that the click probably worked.

*Why:* in a bank, guessing is the expensive outcome. A system that stops is recoverable; a system
that acts on a screen it doesn't understand is an incident.

*Enforced by:* `tests/integration/test_fail_closed.py`, `tests/unit/test_resolver_ambiguity.py`.

### 6. Every sink is redacted
Logs, artifacts, evidence files, screenshots, **and outbound LLM prompts**. Secrets are
`{$secret: ref}` references resolved at dispatch time and never written anywhere.

*Why:* regulated financial data should not leave the process because a model asked for context.

*Enforced by:* `tests/invariants/test_redaction.py` (seeded PII and secrets must appear in zero
sinks).

### 7. Business outcomes are not failures
"No such member" is a legitimate answer the caller needs, not a crash. Every observable condition
must be classified into exactly one of:

| Class | Meaning | Terminal? |
|---|---|---|
| `BUSINESS_OUTCOME` | A real answer from the application (`member_not_found`, `account_closed`) | yes |
| `RECOVERABLE` | A bounded, declared transient condition (`transient_load`, `session_expired`) | no — remediate and retry, then `RECOVERY_EXHAUSTED` |
| `HARD_FAILURE` | A debuggable defect (`target_not_found`, `checkpoint_failed`) | yes |
| `UNEXPECTED_STATE` | Matches nothing the capability declares | yes — fail closed |

*Why:* the brief names conflating the first and third as the most common design mistake in this
problem, and it is: it turns a normal business answer into a page at 2am, and a real defect into a
shrug.

*Enforced by:* `tests/integration/test_fault_matrix.py`.

### 8. Capabilities are immutable
A `Capability` at `id@version` is frozen and content-hashed. Anything that changes because you *ran*
it lives in a separate document: `RunRecord`, `CapabilityEvaluation`, `CapabilityApproval`.

*Why:* a definition that accumulates telemetry stops being reviewable, and "which version actually
ran?" stops being answerable.

*Enforced by:* `tests/contract/test_capability_immutability.py`.

### 9. `domain/` is pure
No I/O, no network, no browser, no clock. `cua.domain` imports nothing else from `cua`.

*Why:* the artifact schema is the centerpiece of this system. It has to be reasonable about, and
testable, in isolation.

*Enforced by:* `.importlinter` contract `pure-domain`.

---

## Architecture map

| Package | Owns | May import |
|---|---|---|
| `cua/domain` | Types only: `Capability`, `TargetDescriptor`, `Action`, `UiSnapshot`, `RunRecord`, taxonomy | nothing from `cua` |
| `cua/perception` | Normalizing a raw surface observation into `UiSnapshot`; fingerprinting | `domain` |
| `cua/targeting` | The resolution ladder, scoring, ambiguity, drift | `domain`, `perception` |
| `cua/policy` | Allowlist, risk tiers, redaction, secrets, `AuthorizedAction` | `domain`, `targeting` |
| `cua/hitl` | `Lease`, `SessionBroker`, intervention queue, operator console | `domain`, `policy` |
| `cua/runtime` | **`dispatcher.py` — the sole importer of `surfaces`** | everything below |
| `cua/surfaces` | `SurfaceDriver` port; Playwright/CDP driver; desktop stub | `domain`, `perception` |
| `cua/agent` | `LlmPort`, prompts, the discovery loop | `runtime` and below |
| `cua/replay` | The deterministic executor | `runtime` and below — **never `agent`** |
| `cua/recorder` | Compiling a discovery trace into a `Capability` | `domain`, `targeting` |
| `cua/evidence` | Structured, redacted, actor-tagged run records | `domain`, `policy` |
| `cua/catalog` | Capability store, tenant overlays, tool-spec export | `domain` |
| `cua/cli` | Entry points | everything |

---

## Commands

```bash
make setup        # venv + deps + chromium
make check        # lint + typecheck + invariants + tests  (what CI runs)
make test         # offline suite; no API key needed
make invariants   # just the architectural contracts
make app          # the hostile mock back-office
make demo         # the full end-to-end story
```

---

## Conventions

- **Types are not optional.** `mypy --strict` passes. Prefer a Pydantic model or a literal union
  over a `dict[str, Any]` at any boundary that outlives a function call.
- **Errors are typed and structured.** No bare `raise Exception`. A failure carries the step, what
  was expected, and what was observed — enough to debug without reproducing.
- **No sleeps in production code.** Wait on a condition, with a declared timeout. `time.sleep` in
  `src/` is a code smell; in `replay/` it is a determinism bug.
- **Tests name the claim they defend.** If you add an invariant, add the test that breaks when
  someone removes it.
- **Comment the *why*, never the *what*.** The code says what. Comments are for the trade-off that
  isn't visible locally.
- **Document what you mock.** Deliberate stubs are fine and expected; undocumented ones read as
  unfinished work.

## Scope discipline

This system deliberately has **no queues, no services, no database, no auth system, and no
multi-tenant infrastructure**. Single process, files on disk. Multi-tenancy is modeled as an overlay
*document*, not as plumbing.

If a change adds infrastructure, it needs to justify itself against the alternative of not existing.
The core abstractions are designed so that scaling out later is possible; building that now is not
the goal.
