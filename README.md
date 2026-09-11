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

## 🚧 Build status

This repository is under active construction. Phases complete so far:

- [x] **Phase 00** — Foundations: toolchain, CI, and the architectural invariants that the rest of
      the system is built to satisfy
- [x] **Phase 01** — Hostile mock back-office app, plus the Phase 03 semantic-tree spike
      pulled forward ([results](docs/prd/phases/phase-03-perception-driver.md))
- [ ] Phase 02 — Domain model & artifact schema
- [ ] Phase 03 — Perception & surface driver
- [ ] Phase 04 — Semantic targeting & resolution ladder
- [ ] Phase 05 — Policy chokepoint, redaction, evidence
- [ ] Phase 06 — Discovery agent loop (real LLM)
- [ ] Phase 07 — Artifact compiler
- [ ] Phase 08 — Deterministic replay engine
- [ ] Phase 09 — HITL escalation & live control transfer
- [ ] Phase 12 — Docs, evidence, submission

Plan: [`docs/prd/phases/`](docs/prd/phases/). Design write-up: [`REPORT.md`](REPORT.md).

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

## Demo path

```bash
make demo           # the whole story, one command  (arrives in Phase 12)
```

Individual commands, each independently runnable:

```bash
make app                                                 # the hostile mock back-office
cua discover --goal "Look up member 12345 and read their current savings balance" \
             --target http://localhost:8811 \
             --out evidence/capabilities/
cua replay <artifact> --input member_id=67890            # SUCCESS + typed outputs
cua replay <artifact> --input member_id=99999            # BUSINESS_OUTCOME, exits 0
cua replay <artifact> --fault transient_load             # RECOVERABLE -> recovered
cua replay <artifact> --fault undeclared_dialog          # UNEXPECTED_STATE -> fails closed
cua console                                              # operator console: take over, act, release
```

---

## Development

```bash
make check          # lint + strict typecheck + architectural invariants + tests  (what CI runs)
make test           # offline suite; no API key, no network
make invariants     # just the architectural contracts
```

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
                  agent · recorder · replay · hitl · evidence · catalog · cli
apps/mock_bank/   the hostile target application (+ a "second tenant" variant)
docs/             ADRs (why) · design (how) · prd (what, in what order) · runbooks
tests/            unit · integration · contract · invariants · e2e(live)
evals/            stability and cross-tenant measurement
evidence/         proof that the end-to-end thread actually ran
```

## Documentation

| Read this | For |
|---|---|
| [`REPORT.md`](REPORT.md) | The design write-up and the trade-offs |
| [`AGENTS.md`](AGENTS.md) | Working agreement, architecture map, the nine invariants |
| [`docs/README.md`](docs/README.md) | Full documentation map |
| [`docs/design/artifact-schema.md`](docs/design/artifact-schema.md) | The centerpiece: the capability artifact |
