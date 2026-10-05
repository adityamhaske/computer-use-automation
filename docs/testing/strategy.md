# Testing strategy

**The rule: every architectural claim has a test that fails when the claim stops being true.**

A design document that asserts an invariant is a wish. A contract in `.importlinter` plus a test that
breaks on violation is an invariant.

## Claim → test

| Claim | Test | Kind |
|---|---|---|
| Replay never calls or imports the LLM | `invariants/test_import_contracts.py`, `integration/test_fault_matrix.py::test_replay_uses_no_model` + `.importlinter:no-llm-in-replay` | static + runtime |
| Policy chokepoint is unbypassable | `invariants/test_policy_chokepoint.py` + `.importlinter:policy-chokepoint` | static + runtime |
| Human control cannot bypass policy or evidence | `invariants/test_human_control_safety.py` | runtime |
| Stale automation cannot act after takeover | `integration/test_handoff.py::test_automation_cannot_act_after_a_human_claims_the_live_session`, `integration/test_dispatcher.py::test_a_stale_lease_epoch_is_refused_before_anything_else` | integration |
| Ambiguous targets are refused, not guessed | `unit/test_resolver.py::test_ambiguity_is_refused_not_tiebroken` | unit |
| Unknown states fail closed | `integration/test_fault_matrix.py::test_an_undeclared_screen_fails_closed` | integration |
| Replay is deterministic | `integration/test_fault_matrix.py::test_repeated_replays_produce_identical_decisions` | integration |
| Every sink is redacted | `invariants/test_redaction.py` | integration |
| Business outcomes are not failures | `integration/test_fault_matrix.py` | integration |
| Targeting is surface-neutral / no CSS dependence | `integration/test_variant_b.py` | integration |
| Capabilities are immutable and content-addressed | `contract/test_capability_schema.py::test_capability_is_frozen`, `::test_tampering_is_detected`, `integration/test_catalog.py::test_an_artifact_edited_in_place_is_refused` | contract |
| The artifact is the tool contract | `contract/test_capability_schema.py::test_exports_an_agent_callable_tool_schema` | contract |
| An irreversible step runs only under a verified, scoped approval | `integration/test_irreversible_approval.py` | integration |
| `run_capability` remedies go through the chokepoint and are bounded | `integration/test_capability_remedy.py`, `integration/test_fault_matrix.py::test_an_expired_session_is_re_authenticated_by_the_declared_remedy` | integration |
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

## Edge-case suites

Alongside the claim-per-test suites above, every subsystem has a `test_edge_<area>_*.py` suite that
feeds it the input a real deployment eventually will: boundary values, empty and whitespace-only
input, non-ASCII digits, homoglyphs, right-to-left and zero-width text, 10,000-character strings,
injection-shaped strings (HTML, SQL, path traversal, format strings, prompt-injection text inside
page content), malformed and partial structured data, repeated and out-of-order calls, illegal state
transitions, stale or forged tokens and epochs, and budgets exactly at their limits. Parametrized
tables keep each claim cheap to read and cheap to extend.

`evidence/tests/edge-case-catalogue.md` lists every claim by name — a test name states the behaviour
it defends, so the catalogue is the specification of what the system is required to handle.

**The command line is tested through its real entry point.** `tests/integration/test_edge_cli_commands.py`
drives `cua` with Typer's `CliRunner`, so argument parsing, exit codes and the wording of every error
are asserted rather than assumed: a missing or tampered artifact, a malformed or repeated `--input`, a
target that is not running, a policy refusal and an unknown capability each produce a sentence and a
meaningful exit code, never a stack trace. This is also what keeps the CLI's coverage honest, instead of
leaving it to the child processes an in-process measurement cannot see.

**Some of it is run for real and kept.** `make edge-cases` (`scripts/run_edge_cases.py`) executes 22
scenarios against a fresh mock app each, through the same executor, policy chokepoint and evidence bus
as `make demo`, and saves every run under `evidence/edge-cases/`: every state a member can be in, the
declared input pattern at and around its boundaries, faults (recovered, bounded, re-authenticated,
failed closed) and governance refusals. A scenario that deviates from what it declares fails the run, so
the saved evidence is also a test.

These suites are how the hardening pass found real defects rather than confirming assumptions. Each
was written as a strict-`xfail` test first, adversarially checked, then fixed in `src/` with the
marker removed; none is left open. Among them: a malformed URL that raised out of the allowlist
instead of being denied; the headline wrong-action metric ignoring the expected status; an
intermittently all-digit identifier redacted as an account number in the trace; a sealed artifact
that reloaded as tampered when it declared an empty list; and an email-redaction pattern that took
minutes on one long unbroken token.

## Does the suite fail when it should? (mutation check)

A passing test only matters if it would fail when the claim stopped being true. `make mutation-check`
(`scripts/mutation_check.py`) breaks the code on purpose — one change at a time, all ten invariants
plus a guard for each defect fixed in the hardening pass — runs the tests that defend it, and records
whether any went red. The source is restored byte-for-byte after each mutation and verified. Anything
that survives is a hole in the suite and fails the run. The latest result is
`evidence/tests/mutation-report.md`.

## Coverage, stated honestly

`make test-report` (`scripts/test_report.py`) runs the whole suite under line and branch coverage and
writes `evidence/tests/test-report.md`. Read it with one caveat: the CLI entry points and the
browser driver run largely in child processes (`make demo`, the UI smoke test), which an in-process
measurement cannot see, so `cua.cli` looks far lower than it is exercised. The end-to-end story is
proven by `make demo`, not by that percentage.

## What is deliberately not tested

- The mock app's own correctness beyond what fixtures need — it is a fixture, not a deliverable.
- Playwright and CDP themselves.
- Load, concurrency, and scale. There is no concurrency in this system by design.

---

[Repository](https://github.com/adityamhaske/computer-use-automation) · [Documentation](https://adityamhaske.github.io/computer-use-automation/) · [Design write-up](https://adityamhaske.github.io/computer-use-automation/report/)
