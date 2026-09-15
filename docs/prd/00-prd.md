# PRD — Computer-Use Automation System

> **Thesis: discovery is probabilistic; execution is deterministic.**

## 1. Problem

Banks and credit unions run a long tail of back-office applications with **no API**: core banking
screens, servicing tools, admin consoles. When an AI agent needs to get real work done in one of
them, the only way in is to drive the UI the way a human operator would.

Doing that with an LLM on every invocation is slow, expensive, non-deterministic, and unauditable —
three of which are disqualifying in a regulated environment.

## 2. Solution shape

Use the model **once**, to discover how a task is accomplished. Compile what it learned into a
typed, versioned, reviewable **capability artifact**. From then on, execute that artifact
deterministically with no model in the loop.

```
goal + target ──► LLM discovery run ──► capability artifact ──► deterministic replay ──► outputs
                        (once)            (reviewed, versioned)      (every time after)
```

## 3. Users

| User | Needs |
|---|---|
| **A calling AI agent** | To invoke a capability by name with typed arguments and get a typed result back, including "no such member" as a legitimate answer |
| **A human reviewer** | To read a capability and understand what it does, what it touches, and what could go wrong — without running it |
| **A human operator** | To be handed a stuck session with enough context to act, take control of that live session, and hand it back |
| **An engineer on call** | To read a failed run and know what step, what was expected, what was observed |

## 4. Requirements

Derived from the assignment brief §3. Full traceability in
[01-requirements-traceability.md](01-requirements-traceability.md).

### Must have

| # | Requirement |
|---|---|
| R1 | Accept a natural-language goal + target; run an LLM observe→decide→act loop against a **live** UI until the goal is met or a stopping condition fires |
| R2 | Emit a typed, versioned, serializable artifact: ordered steps, how each control is identified, typed inputs, typed outputs, a success checkpoint |
| R3 | Replay that artifact deterministically **with no LLM in the decision loop**, using stable targeting, verifying the checkpoint, returning declared outputs |
| R4 | Distinguish expected business outcomes from recoverable conditions from hard failures, and report a structured result with enough detail to debug |
| R5 | Enforce a configurable allowlist; classify and conservatively handle risky/irreversible actions; never persist secrets or raw PII |
| R6 | Produce evidence: a structured log of what happened and why, plus a richer signal on failure |
| R7 | Detect "stuck", route an intervention with context, let a human take control of the **live** session, and hand control back |
| R8 | Design (not necessarily build) a credible answer for heterogeneous surfaces and multi-tenant reuse |

### Explicitly out of scope

Real bank systems. API-based integration. Queues, clusters, services, databases. Real multi-tenant
infrastructure. Operator authentication and routing. A production co-browsing console.

## 5. Success criteria

The submission is strong if and only if all five hold:

1. **A real LLM discovery run exists in `/evidence/`** — the brief's one non-negotiable.
2. **The artifact schema is genuinely good** — typed I/O, semantic targeting, an asserted checkpoint,
   and the outcome/recovery/failure split visible *in the schema itself*.
3. **Replay demonstrably never touches the LLM**, and a fault matrix shows the taxonomy working.
4. **A human really takes over the live session and the run resumes** — policed and evidenced.
5. **`REPORT.md` defends the trade-offs** under the seven mandated headings, including the cuts.

## 6. Key decisions

| Area | Choice | ADR |
|---|---|---|
| Perception | Normalized `UiSnapshot`; accessibility tree is a driver detail | [0001](../adr/0001-uisnapshot-as-the-cross-surface-abstraction.md) |
| Artifact | Immutable, content-addressed; telemetry lives in sibling documents | [0002](../adr/0002-immutable-content-addressed-capability.md) |
| Errors | Four observation classes, separate from four run statuses; fail closed | [0003](../adr/0003-four-class-observation-taxonomy.md) |
| Safety | One type-enforced policy chokepoint for all three actors | [0004](../adr/0004-single-policy-chokepoint.md) |
| Tenancy | Overlay documents, never forks; drift measured per step | [0005](../adr/0005-tenant-overlay-not-fork.md) |
| Stack | Python 3.11, Pydantic v2, Playwright/CDP, FastAPI, OpenRouter behind `LlmPort` | — |
| Target | A locally-built, deliberately hostile mock back-office app + a "second tenant" variant | — |

## 7. Target application

We do not have — and must not seek — access to a real bank system. The proxy target is a local mock
back-office app, built hostile on purpose: framesets, table-based layout, non-semantic markup,
inline `onclick`, and **no test IDs**.

Chosen over a public demo site because it is the only option that lets us **inject** the exceptional
states the brief requires evidence of (validation errors, record-not-found, permission denial,
surprise dialogs, session expiry, transient failures), build a second "tenant" variant, run
offline in CI, and avoid any terms-of-service or real-PII concerns.

It is hostile but **bounded**: deterministic by construction, with nondeterminism only behind
explicit fault flags, so failures are debuggable rather than flaky.

## 8. Non-goals

Feature breadth. Framework name-dropping. Scaling infrastructure. The brief states plainly that none
of these are rewarded, and that "a small, correct, well-argued system is the goal."

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
