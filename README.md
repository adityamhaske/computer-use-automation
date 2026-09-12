# Computer-Use Automation System (CUA)
### Deterministic Execution Engine for Regulated Banking Applications

> **Discovery is probabilistic. Execution is deterministic.**

🌐 **Live Documentation & Interactive Demo:** [https://adityamhaske.github.io/interface.ai/](https://adityamhaske.github.io/interface.ai/)  

---

An LLM works out how to accomplish a goal in a legacy application that has no API — **once**. That discovery run is compiled into a typed, versioned **capability artifact**. From then on the artifact is replayed deterministically, with **no model in the decision loop**, and invoked by AI agents as a normal typed function.

Built for mission-critical back-office applications at banks and credit unions, where the only path to automation is driving the live UI with strict regulatory compliance, complete auditability, and zero data leakage.

```
Goal + Target ──► LLM Discovery Run ──► Capability Artifact ──► Deterministic Replay ──► Typed Outputs
                       (once)             (reviewed, sealed)        (every time after)
                                                                            │
                                                              stuck? ───────┴──► Human takes the
                                                                                 live session, acts,
                                                                                 hands it back
```

---

## Unified Entrypoint (`./start.sh`)

The repository includes a unified orchestrator script [`./start.sh`](start.sh) to run every mode end to end:

```bash
./start.sh                 # Default: setup venv + run the full 12-stage demo
./start.sh ui              # Launch the full Human-in-the-Loop Web Console + Mock Back-Office
./start.sh ui --headed     # Run the console with visible (headed) Chromium browser
./start.sh app             # Run just the hostile mock back-office application (port 8811)
./start.sh app-b           # Run the rebranded / restyled second-tenant variant (port 8811)
./start.sh check           # Run CI checks: lint + strict typecheck + invariants + tests
./start.sh test            # Run offline test suite (no API keys, no external network)
./start.sh eval            # Run stability and cross-tenant drift evaluations
```

---

## 12-Stage Verified Execution Story

Run the full end-to-end demonstration locally with:

```bash
make setup          # venv, dependencies, Playwright Chromium
make demo           # the complete 12-stage story in ~60 seconds
```

`make demo` boots the mock back-office and executes all 12 stages sequentially, writing evidence to [`evidence/`](evidence/):

| # | Stage | Observable Behavior | Contract / Result |
|---|---|---|---|
| **1** | Mock Back-Office Running | Hostile frameset application, zero test IDs | `READY` |
| **2** | LLM Discovery | Agent drives live UI to goal, discovering path | `TRACE` |
| **3** | Capability Compiled | Typed I/O, semantic descriptors, content hash | `SEALED` |
| **4** | Deterministic Replay | New member inputs, 0 model calls in loop | `SUCCESS` |
| **5** | Business Outcome | `member_not_found` — valid answer, not a crash | `EXIT 0` |
| **6** | Injected 502 Fault | Declared recovery rule remediates and retries | `SUCCESS` (`recovery_attempts=1`) |
| **7** | Fault Past Budget | Recovery exhausted → escalates to human queue | `NEEDS_HUMAN(recovery_exhausted)` |
| **8** | Malformed Input | Input schema guard rejects before dispatch | `FAILED` (browser never moved) |
| **9** | Undeclared Screen | Unrecognized state fails closed and escalates | `NEEDS_HUMAN(unexpected_state)` |
| **10** | Human Takeover | Operator drives same session with Lease lock | `actor=human, epoch=3` |
| **11** | Re-Anchor Resume | Resumes at the right step without replaying | `SUCCESS` |
| **12** | Agent Tool Call | Exposed to caller agents as a typed tool | `SUCCESS` |

> **Offline Verifiability:** Works 100% offline without an API key — discovery falls back to a verified recorded transcript. Set `OPENROUTER_API_KEY` in `.env` for live LLM exploration.

---

## Human-in-the-Loop (HITL) Web Console

The system includes a production operator console built for supervisors and bank operators (`cua.hitl.console`):

* **Launch:** `./start.sh ui` (starts mock bank on `:8811` and console on `:8812`)
* **Live Dashboard:** Real-time run tracking, active intervention queue, and session health.
* **Live Screen & Control:** Operator claims session via exclusive lease token (`Lease`), drives the UI through the policy chokepoint, and releases control back to automation.
* **Re-Anchor Architecture:** When the human finishes intervening, the engine reconciles the current screen state and resumes at the appropriate step, avoiding re-running past mutations.
* **Audit Trail:** Every keystroke, click, and state change made by a human is recorded on the evidence bus under `actor="human"` with complete redaction.

---

## CLI Reference

Every capability is accessible directly via the `cua` CLI:

```bash
# 1. Discover a new workflow against a live application surface (requires LLM key)
cua discover --goal "Look up member 12345 and read their current savings balance" \
             --target http://localhost:8811 \
             --out evidence/capabilities/

# 2. Replay the compiled capability deterministically (zero model calls)
cua replay evidence/capabilities/lookup_member@1.0.0.json \
           --base-url http://localhost:8811 \
           --input member_id=67890

# 3. Supervise and claim sessions in the operator console
cua console --target http://localhost:8811

# 4. Capability Catalog management and tool execution
cua catalog list                                    # List available frozen capabilities
cua catalog show lookup_member --tool-schema        # Export JSON Schema tool contract
cua catalog invoke lookup_member --input member_id=12345 --base-url http://localhost:8811
```

### Exit Code Contract
* `0`: Success **and** valid business outcome (`member_not_found`, `account_closed`).
* `1`: Hard failure / defect (`target_not_found`, `checkpoint_failed`).
* `2`: Escalation required (`unexpected_state`, `recovery_exhausted`).

---

## Architectural Invariants

The architecture enforces ten mechanical invariants. These are verified by import linter rules and continuous contract tests:

1. **Replay never calls or imports the LLM:** `cua.replay` has zero imports of `cua.agent` or model clients. Replay is 100% deterministic and auditable.
2. **No action reaches a driver without policy authorization:** Every action must pass through `TargetResolver` and receive an `AuthorizedAction` token from `PolicyEngine` before dispatch.
3. **Target identity is semantic, never raw CSS selectors:** Controls are defined by role, accessible name, and structural anchors. CSS selectors are only unverified caches.
4. **Human input cannot bypass policy or evidence:** Operator inputs pass through the same policy chokepoint and evidence bus under the `HUMAN` policy profile.
5. **Unknown UI states fail closed:** Unmatched screens raise `UNEXPECTED_STATE` and halt execution immediately.
6. **Every sink is redacted:** PII, account numbers, and secrets are stripped from logs, traces, screenshots, and LLM prompts.
7. **Business outcomes are not failures:** Application-level responses are classified separately from execution crashes.
8. **Automation may only act while holding the session lease:** If an operator claims the session, automation dispatch is refused with `LEASE_LOST`.
9. **Capabilities are immutable:** Sealed artifacts are content-hashed (SHA-256) and frozen.
10. **Domain layer is pure:** `cua.domain` has zero I/O, network, or external dependencies.

---

## Multi-Tenant Adaptation & Overlays

In enterprise banking, different tenant institutions often run the same core back-office software with customized labels, headers, and CSS.

* **TenantBinding Overlays:** Rather than re-recording workflows for every tenant, a four-line overlay document maps tenant-specific labels.
* **Drift Measurement:** `make eval` runs cross-tenant evaluation against variant applications (`apps/mock_bank/tenant_b.py`) and quantifies drift score before replays fail.

---

## Repository Structure

```
site/                  Interactive documentation and architecture portal (GitHub Pages)
src/cua/
  domain/              Pure domain types: Capability, Action, TargetDescriptor, RunRecord
  perception/          Surface normalization into UiSnapshot and accessibility trees
  targeting/           6-rung semantic target resolution ladder and ambiguity detector
  policy/              Policy chokepoint, allowlist, risk tiers, and zero-leak redactor
  hitl/                SessionBroker, Lease protocol, and Operator Web Console
  runtime/             Dispatcher: exclusive importer of surface drivers
  surfaces/            SurfaceDriver implementations (Playwright / CDP driver)
  agent/               LLM discovery loop, vision/DOM navigation, prompt compiler
  replay/              Deterministic replay engine (strictly no LLM imports)
  recorder/            Trace-to-capability compilation and content-hashing
  evidence/            Structured audit bus and redacted run records
  catalog/             Capability catalog, tenant overlays, and tool export
  cli/                 CLI entrypoints (discover, replay, console, catalog)
apps/mock_bank/        Hostile banking target application (framesets, no test IDs) + Variant B
docs/                  Architecture specs, ADRs (0001-0005), PRD, runbooks, and traceability
evidence/              Committed execution evidence, traces, evaluations, and artifacts
tests/                 Unit, integration, contract, invariant, and fault matrix test suites
```

---

## Documentation Index

| Resource | Purpose |
|---|---|
| [**Interactive Documentation Portal**](https://adityamhaske.github.io/interface.ai/) | **Complete web documentation, interactive execution pipeline, and guides** |
| [`REPORT.md`](REPORT.md) | Architectural trade-offs, design rationale, and technical retrospective |
| [`AGENTS.md`](AGENTS.md) | Working agreement, invariant specifications, and test contracts |
| [`evidence/README.md`](evidence/README.md) | What each committed run proves and how to read evidence traces |
| [`docs/README.md`](docs/README.md) | Full documentation index across ADRs, design, and runbooks |
| [`docs/design/artifact-schema.md`](docs/design/artifact-schema.md) | Complete Capability artifact schema and specification |
| [`docs/design/target-resolution.md`](docs/design/target-resolution.md) | Six-rung semantic target resolution ladder |
| [`docs/prd/01-requirements-traceability.md`](docs/prd/01-requirements-traceability.md) | Requirements traceability matrix (R1–R10) |

---

## Proprietary Notice & License

**PROPRIETARY AND CONFIDENTIAL**

Copyright © 2026 Aditya Mhaske / interface.ai. All rights reserved.

This software, its source code, documentation, specifications, and associated assets are proprietary and confidential. **This is not an open-source project.** Unauthorized copying, modification, distribution, transmission, reverse engineering, or disclosure of this software, in whole or in part, via any medium is strictly prohibited without prior written authorization from the copyright holder.
