# Computer-Use Automation System

> **Discovery is probabilistic. Execution is deterministic.**

An LLM works out how to accomplish a goal in a legacy application that has no API — once. That run
is compiled into a typed, versioned **capability artifact**. From then on the artifact is replayed
deterministically, with no model in the decision loop, and invoked by AI agents as a normal typed
function.

Built as a take-home for interface.ai. Target domain: back-office applications at banks and credit
unions, where the only way in is to drive the UI the way a human operator would.

```
goal + target ──► LLM discovery run ──► capability artifact ──► deterministic replay ──► typed outputs
                        (once)           (reviewed, versioned)      (every time after)
                                                                            │
                                                              stuck? ───────┴──► human takes the
                                                                                 live session, acts,
                                                                                 hands it back
```

---

## Try it

```bash
make setup          # venv, dependencies, Chromium
make demo           # the whole story, one command
```

`make demo` boots the mock back-office and runs twelve stages end to end, writing everything to
[`evidence/`](evidence/):

```
  1. Mock back-office running                              hostile frameset app, no test ids
  2. Discovery                                             the model drives the live UI to the goal
  3. Capability compiled and sealed                        typed I/O, semantic targets, content hash
  4. Deterministic replay, new inputs                      SUCCESS  — no model in the loop
  5. Business outcome (exit 0 — an answer, not a crash)    BUSINESS_OUTCOME(member_not_found)
  6. Injected 502 — declared recovery cleared it           SUCCESS  recovery_attempts=1
  7. Same fault past its declared budget                   NEEDS_HUMAN(recovery_exhausted)
  8. Malformed input rejected before acting                FAILED — the browser never moved
  9. Undeclared screen — failed closed and escalated       NEEDS_HUMAN(unexpected_state)
 10. Operator drove the same live session, handed it back  actor=human, lease epoch 3
 11. Re-anchored after the handoff                         resumes at the right step, not step 1
 12. An agent called it by name                            typed args, declared outcomes, no model
```

**It works without an API key.** Stage 2 falls back to a recorded transcript and says so, in the
output and in the evidence. Everything after it is model-free by construction, so the demo is not
pretending. Set `OPENROUTER_API_KEY` and re-run for a genuine LLM-driven discovery.

Takes about a minute. Then read [`REPORT.md`](REPORT.md) and
[`evidence/README.md`](evidence/README.md).

---

## Setup

```bash
make setup          # venv, dependencies, Chromium
```

Requires Python 3.11+. `uv` is used if present, otherwise `venv` + `pip`.

### Configuration

```bash
cp .env.example .env
```

**The only thing that needs an API key is the live discovery run.** Replay, escalation, the full
test suite, and the demo all work without one — the demo falls back to a recorded transcript and
says so explicitly. CI is configured with no key on purpose: if a test needs the model, CI fails.

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Discovery only. Any OpenAI-compatible gateway works. |
| `CUA_LLM_BASE_URL` | Defaults to OpenRouter |
| `CUA_LLM_MODEL` | Model id for discovery |
| `CUA_POLICY_FILE` | Allowlist and risk policy — defaults to `config/policy.yaml` |

---

## The commands underneath

`make demo` is orchestration, not a second implementation — every stage is a command you can run on
its own:

```bash
make app                                      # the hostile mock back-office, on :8811
make app-variant-b                            # the same product, rebranded and restyled: a second tenant

cua discover --goal "Look up member 12345 and read their current savings balance" \
             --target http://localhost:8811 \
             --out evidence/capabilities/     # needs OPENROUTER_API_KEY

cua replay <artifact> --base-url http://localhost:8811 --input member_id=67890   # SUCCESS
cua replay <artifact> --base-url http://localhost:8811 --input member_id=99999   # BUSINESS_OUTCOME, exits 0

cua console --target http://localhost:8811    # operator console: claim, act, release

cua catalog list                              # what an agent can call, with typed signatures
cua catalog show <id> --tool-schema           # the agent-facing tool contract
cua catalog invoke <id> --input member_id=12345 --base-url http://localhost:8811
cua agent-demo --base-url http://localhost:8811   # a caller handling all four statuses
```

Exit codes are part of the contract: `0` success **and** business outcome, `1` failed, `2` needs a
human. A business outcome is an answer, not an incident.

Faults are injected into the mock app rather than passed to the replay engine — the executor must
not know a fault is coming, or the error-handling demonstration would be staged:

```bash
curl -X POST localhost:8811/_control/arm -d '{"fault":"transient_load","count":1}' -H 'content-type: application/json'
curl -X POST localhost:8811/_control/reset
```

Available faults: `transient_load`, `session_timeout`, `undeclared_dialog`, `validation_error`.

---

## Development

```bash
make check          # lint + strict typecheck + architectural invariants + tests  (what CI runs)
make test           # offline suite; no API key, no network
make invariants     # just the architectural contracts
make eval           # stability + cross-tenant measurement -> evidence/evals/
```

244 tests, `mypy --strict` clean, five enforced import contracts. The whole suite runs **offline
with no API key** — the fake-LLM harness replays recorded transcripts, so a reviewer with no
credentials can run everything except the one live-model test (`make test-live`).

### The invariants

The value of this system is almost entirely in a handful of properties, so they are enforced
mechanically rather than by convention. [`AGENTS.md`](AGENTS.md) documents all nine; the load-bearing
ones:

| Invariant | Enforced by |
|---|---|
| Replay never calls or imports the LLM | `.importlinter` + a test that replays with the client patched to raise |
| No action reaches a driver without policy authorization | import rule + a type only `PolicyEngine` can mint + an authorize↔dispatch reconciliation test |
| Target identity is semantic, never a CSS selector | import rule + a "second tenant" variant that invalidates every cached selector |
| Human input cannot bypass policy or evidence | the console submits actions; it never injects them |
| Unknown UI states fail closed | the executor stops rather than assuming the click worked |

Run `make invariants` to check them. Break one on purpose to watch it fail — that is the point.

---

## Layout

```
src/cua/          domain · perception · surfaces · targeting · policy · runtime
                  agent · recorder · replay · hitl · evidence · cli
apps/mock_bank/   the hostile target application (+ a "second tenant" variant)
docs/             ADRs (why) · design (how) · prd (what, in what order) · runbooks
tests/            unit · integration · contract · invariants · e2e(live)
evidence/         proof that the end-to-end thread actually ran  (start here)
                  including evals/ — the measured numbers behind the claims
```

## Documentation

| Read this | For |
|---|---|
| [`REPORT.md`](REPORT.md) | The design write-up and the trade-offs — **read this first** |
| [`evidence/README.md`](evidence/README.md) | What each committed run proves, and how to read one |
| [`AGENTS.md`](AGENTS.md) | Working agreement, architecture map, the nine invariants |
| [`docs/README.md`](docs/README.md) | Full documentation map |
| [`docs/design/artifact-schema.md`](docs/design/artifact-schema.md) | The centerpiece: the capability artifact |
