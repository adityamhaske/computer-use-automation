# Testing strategy

**The rule: every architectural claim has a test that fails when the claim stops being true.**

A design document that asserts an invariant is a wish. A contract in `.importlinter` plus a test that
breaks on violation is an invariant.

## Claim → test

| Claim | Test | Kind |
|---|---|---|
| Replay never calls or imports the LLM | `invariants/test_no_llm_in_replay.py` + `.importlinter:no-llm-in-replay` | static + runtime |
| Policy chokepoint is unbypassable | `invariants/test_policy_chokepoint.py` + `.importlinter:policy-chokepoint` | static + runtime |
| Human control cannot bypass policy or evidence | `invariants/test_human_control_safety.py` | runtime |
| Stale automation cannot act after takeover | `unit/test_lease.py` | unit |
| Ambiguous targets are refused, not guessed | `unit/test_resolver_ambiguity.py` | unit |
| Unknown states fail closed | `integration/test_fail_closed.py` | integration |
| Replay is deterministic | `invariants/test_determinism.py` | integration |
| Every sink is redacted | `invariants/test_redaction.py` | integration |
| Business outcomes are not failures | `integration/test_fault_matrix.py` | integration |
| Targeting is surface-neutral / no CSS dependence | `integration/test_variant_b.py` | integration |
| Capabilities are immutable and content-addressed | `contract/test_capability_immutability.py` | contract |
| The artifact is the tool contract | `contract/test_json_schema_export.py` | contract |
| `domain/` is pure | `.importlinter:pure-domain` | static |

## Layers

| Layer | Proves | Cost |
|---|---|---|
| `unit/` | Pure logic: schema, resolver rungs, scoring, policy decisions, redaction rules, lease transitions, taxonomy classification | free |
| `integration/` | Full discovery → artifact → replay against the mock app, driven by a **fake LLM** replaying recorded transcripts | free, deterministic |
| `contract/` | Schema stability: every artifact in `evidence/` validates; JSON Schema export is golden | free |
| `invariants/` | The architectural claims above | free |
| `e2e/` | One real LLM run against a live browser | `@pytest.mark.live`, opt-in |

## Offline by default

Everything except `e2e/` runs with **no API key and no network**. `pytest` deselects `live` by
default (`-m 'not live'`).

This is deliberate and load-bearing: a reviewer with no OpenRouter account can still run the full
suite, the full replay demo, and the escalation demo. CI configures **no API key on purpose** — if a
test needs the model, CI must fail rather than silently skip.

## The fake LLM

`agent/fake.py` implements `LlmPort` by replaying a recorded transcript. This exercises the *real*
discovery loop — the same policy checks, the same resolver, the same evidence bus — for free.

Its risk is fixture drift: recorded transcripts silently diverging from what the live port produces,
so CI passes while production is broken. Mitigated by regenerating fixtures from a live run via
script, plus a contract test asserting fixture shape matches the live `LlmPort` schema.

## What is deliberately not tested

- The mock app's own correctness beyond what fixtures need — it is a fixture, not a deliverable.
- Playwright and CDP themselves.
- Load, concurrency, and scale. There is no concurrency in this system by design.
