# Phase 05 — Policy chokepoint, redaction, evidence ⭐

**Objective.** Build the single authorization path every actor must traverse, and the redacted,
actor-tagged evidence trail that proves they did.

Reference: [ADR 0004](../../adr/0004-single-policy-chokepoint.md).

## Scope

```
Action ──► PolicyEngine ──► TargetResolver ──► SurfaceDriver
```

| Module | Responsibility |
|---|---|
| `policy/authorized.py` | `AuthorizedAction` — carries a token instance only `PolicyEngine` holds |
| `policy/engine.py` | `authorize(action, ctx) -> AuthorizedAction \| Deny \| RequireApproval`; per-actor profiles |
| `policy/allowlist.py` | Domains, URL patterns, action types, step/duration caps (`config/policy.yaml`) |
| `policy/risk.py` | Three-signal classifier: action type × control semantics (danger lexicon) × artifact annotation. Highest tier wins. |
| `policy/redact.py` | Rule-based + `sensitive`-marked redaction at **every** sink, including outbound LLM prompts |
| `policy/secrets.py` | `{$secret: ref}` resolved at dispatch; never written anywhere |
| `runtime/dispatcher.py` | **The only module permitted to import `cua.surfaces`** |
| `evidence/bus.py` | Append-only JSONL: authorize / resolve / dispatch / observe, each tagged `actor`, `session_id`, `lease_epoch` |
| `evidence/capture.py` | Screenshots + snapshot dumps, redacted; richer capture on failure |

## The per-actor profile

`AUTOMATION` and `HUMAN` get different profiles, not different code paths. A human may confirm an
irreversible action; automation may not. Both are bound by the domain allowlist and both are
recorded. See the table in ADR 0004.

## Exit criteria

- [ ] A hand-constructed `AuthorizedAction` is **rejected** by `dispatch()`
- [ ] Off-allowlist navigation ⇒ `NAVIGATION_BLOCKED`, not followed
- [ ] Irreversible action classified correctly from each of the three signals independently
- [ ] **Redaction test finds zero leaks** — seeded secrets and PII appear in no log, artifact,
      evidence file, screenshot manifest, or captured LLM prompt
- [ ] Every `dispatch` event in a run record has a matching `authorize` event
- [ ] `.importlinter` `policy-chokepoint` passes with real imports present

## Risks

| Risk | Mitigation |
|---|---|
| Redaction that is thorough enough to be useless for debugging | Redact *values*, keep *shapes*: `member_id=<redacted:string[5]>`. A debugger needs to know a field was present and well-formed. |
| The token guard is trivially bypassable in Python | True — it stops accidents, not adversaries. The import rule and the reconciliation test are the real enforcement. Stated as a limit in REPORT.md §6. |
