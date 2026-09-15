# Phase 12 — Docs, evidence capture, submission

**Objective.** Make the work legible in the first five minutes, and prove it ran.

**Communication is a graded criterion.** The system can be excellent and still score badly if the
reviewer cannot run it or follow the reasoning.

## Scope

### `/README.md` (brief deliverable 1 — exact path)
- Setup, including keys/config
- **How to run without live services** — the full suite and replay demo work with no API key
- The demo path: exact commands to run the agent on a goal, then replay the artifact

### `/REPORT.md` (brief deliverable 2 — exact path, seven mandated headings, verbatim)
1. Architecture 2. Artifact schema 3. Determinism & error handling
4. Heterogeneity & multi-tenant 5. Escalation & handoff 6. Safety 7. Cuts

1–3 pages. Every claim traceable to code or evidence.

### `/evidence/` (brief deliverable 3 — exact path)
- The saved capability artifact
- Discovery run: trace, LLM calls, snapshots, screenshots, run record
- Replay runs: a success, **a business outcome**, and **an error/fault path**
- An escalation run: intervention, human actions, `human_delta`, resume
- `evidence/README.md` indexing all of it — what each run shows and how to read it

### `make demo`
Built **from commands that already work independently**, so it is an orchestration rather than a
second implementation. Must tolerate a missing API key by falling back to a recorded transcript and
**saying which mode it ran in**.

## Exit criteria

- [ ] A cold clone reaches a working demo via `make setup && make demo`
- [ ] Every brief §3 requirement traces to code + test + evidence
      ([traceability matrix](../01-requirements-traceability.md))
- [ ] `REPORT.md` uses the seven headings verbatim and states the cuts honestly
- [ ] No secrets in the repo; `evidence/` verified clean of PII
- [ ] Repository is **public**; link emailed to `assignments@interface.ai` on its own line

## Risks

| Risk | Mitigation |
|---|---|
| `make demo` becomes a fragile 10-stage script that breaks on grading day | Build it last, from working commands; each stage independently runnable; tolerate a missing key. |
| Evidence accidentally contains real-looking PII | Seeded fake data only, plus the redaction invariant test run against `evidence/`. |
| Writing the report last, badly, under time pressure | ADRs are written as decisions are made; REPORT.md assembles them rather than inventing reasoning after the fact. |

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
