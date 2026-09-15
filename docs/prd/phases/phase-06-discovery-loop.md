# Phase 06 — Discovery agent loop (real LLM) ⭐

**Objective.** An LLM drives the live mock app to a natural-language goal, and every action it takes
goes through the same chokepoint replay will use.

**The brief's one non-negotiable.** At least one genuine LLM-driven run against a live surface, with
evidence. Everything else may be mocked; this may not.

## Scope

| Module | Responsibility |
|---|---|
| `agent/llm.py` | `LlmPort` — OpenAI-compatible chat-completions + tool-calling over OpenRouter. `base_url`/`model` from env. |
| `agent/tools.py` | The closed action space exposed as tool definitions — **the model cannot invent an action** |
| `agent/prompts/` | System prompt, observation rendering, goal framing |
| `agent/loop.py` | observe → decide → act, with budget accounting |
| `agent/stop.py` | Stopping conditions: goal met, max steps, timeout, token budget, dead-end (no progress in N steps) |
| `evidence/bus.py` | Full LLM call capture as `llm_call` events in `trace.jsonl` — **redacted outbound** |
| `agent/fake.py` | Fake `LlmPort` replaying recorded transcripts, so CI exercises the real loop for free |

## Design notes

- The model sees a **rendered `UiSnapshot`**, not raw HTML. It reasons in the same vocabulary the
  artifact will store, which is what makes the trace compilable in Phase 07.
- Outbound redaction runs *before* the prompt leaves the process.
- Page text is untrusted input. The closed action space plus policy-outside-the-model is the
  injection defense; the model cannot widen the allowlist or approve its own risky action.

## Exit criteria

- [x] CI runs the identical loop against `agent/fake.py` — no key, no tokens, no network
- [x] Every action in the run traversed `PolicyEngine` (asserted from the run record)
- [x] Stop conditions each individually tested
- [x] No secret or PII appears in any captured prompt
- [x] Every synthesized descriptor is verified by resolving it back, during the run
- [ ] **A real run against a real browser completes a real goal** — test written and deselected;
      needs `OPENROUTER_API_KEY`. This is the brief's one non-negotiable and the only outstanding
      item in this phase.

### Verification record

174 tests green; `mypy --strict` on 59 files; 5/5 contracts over 258 dependencies.

**Descriptors are verified by use.** The model points at a node it can see; we describe that node
semantically; the resolver then has to find the same node from the description alone. A failure is
caught while a model is still in the loop, rather than as a capability that breaks on its first
replay weeks later. `synthesize_descriptor` is shared with the Phase 07 compiler deliberately — what
gets written into the artifact is exactly what was proven to resolve during discovery, not a second
guess at it.

That check immediately earned its place by finding **three real bugs**:

1. **`ADJACENT_TO` returned every following sibling**, not the next one. A four-column account row
   offered three candidates for "the cell after Savings", and the resolver — correctly refusing
   ambiguity — failed every extraction from a table wider than two columns.
2. **An anchor was offered as a candidate for itself.** The exclusion compared object identity
   rather than node id, so every two-cell label/value lookup came back ambiguous.
3. **`ROW_OF` is the wrong relation for reading a value.** Acting on a control wants "the control in
   the row labelled X"; reading a value wants "the cell immediately after X". The relation is now
   chosen by the target's role.

**A gap the first end-to-end run exposed:** only *actionable* roles carried ids in the rendered page,
so the model could not reference the balance cell it needed to read — the capability's whole output
was inexpressible. Readable roles now carry ids too. The extra tokens are worth less than the
capability.

**Two more chokepoint violations, both caught by `test_policy_chokepoint.py` rather than by
import-linter** (the AST scan sees files that are not yet in the import graph): `cli/wiring.py`
constructed a driver, and the import-linter contract itself was wrong — it forbade *indirect* reach,
which would mean nothing could ever act through the dispatcher at all. The contract now bans direct
imports only, and the AST scan covers the rest.

Both violations were fixed by moving the module rather than adding an exemption. Each exemption is
defensible alone; together they turn a rule you can check into a rule you have to argue about.

## Risks

| Risk | Mitigation |
|---|---|
| Model loops or wanders | Hard budgets: max steps, wall clock, tokens; dead-end detection on no-progress. |
| Recorded fixtures drift from the live loop and silently stop testing production | Regenerate fixtures from a live run via script; a contract test asserts fixture shape matches the live `LlmPort` schema. |
| No API key available at build time | Everything else is buildable and testable offline; the live run is a discrete, cheap final step. It must happen before submission. |
