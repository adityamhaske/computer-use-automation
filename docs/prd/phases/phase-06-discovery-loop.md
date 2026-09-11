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
| `agent/transcript.py` | Full LLM call capture to `llm_calls.jsonl` — **redacted outbound** |
| `agent/fake.py` | Fake `LlmPort` replaying recorded transcripts, so CI exercises the real loop for free |

## Design notes

- The model sees a **rendered `UiSnapshot`**, not raw HTML. It reasons in the same vocabulary the
  artifact will store, which is what makes the trace compilable in Phase 07.
- Outbound redaction runs *before* the prompt leaves the process.
- Page text is untrusted input. The closed action space plus policy-outside-the-model is the
  injection defense; the model cannot widen the allowlist or approve its own risky action.

## Exit criteria

- [ ] A real run against a real browser completes a real goal; transcript in `evidence/discovery/`
- [ ] CI runs the identical loop against `agent/fake.py` — no key, no tokens, no network
- [ ] Every action in the run traversed `PolicyEngine` (asserted from the run record)
- [ ] Stop conditions each individually tested
- [ ] No secret or PII appears in any captured prompt

## Risks

| Risk | Mitigation |
|---|---|
| Model loops or wanders | Hard budgets: max steps, wall clock, tokens; dead-end detection on no-progress. |
| Recorded fixtures drift from the live loop and silently stop testing production | Regenerate fixtures from a live run via script; a contract test asserts fixture shape matches the live `LlmPort` schema. |
| No API key available at build time | Everything else is buildable and testable offline; the live run is a discrete, cheap final step. It must happen before submission. |
