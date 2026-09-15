# Phase 05 — Policy chokepoint, redaction, evidence ⭐

**Objective.** Build the single authorization path every actor must traverse, and the redacted,
actor-tagged evidence trail that proves they did.

Reference: [ADR 0004](../../adr/0004-single-policy-chokepoint.md).

## Scope

```
Action ──► TargetResolver ──► PolicyEngine ──► SurfaceDriver
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

- [x] A hand-constructed `AuthorizedAction` is **rejected** by `dispatch()`
- [x] Off-allowlist navigation ⇒ `NAVIGATION_BLOCKED`, not followed
- [x] Irreversible action classified correctly from each of the three signals independently
- [x] **Redaction test finds zero leaks** — asserted by sweeping every file the run wrote, not by
      checking the redactor in isolation
- [x] Every `dispatch` event in a run record has a matching `authorize` event
- [x] `.importlinter` `policy-chokepoint` passes with real imports present (185 dependencies)
- [x] Stale lease epoch refused before anything else happens

### Verification record

162 tests green (21 invariant, 18 policy unit, 11 dispatcher integration). `mypy --strict` clean on
49 files; 5/5 contracts.

**Pipeline order corrected.** The plan specified `Action → PolicyEngine → TargetResolver →
SurfaceDriver`. Risk classification's second signal is *what the control says it does*, which
requires the resolved node — so authorizing first classifies on action type alone. The real order is
`Action → TargetResolver → PolicyEngine → SurfaceDriver`. This does not weaken the chokepoint:
resolution is a pure function over an already-captured snapshot, and the invariant was never "policy
runs first" but *nothing reaches a surface without authorization*. AGENTS.md, ADR 0004 and the
design docs were amended with the reasoning recorded.

**Two bugs my own tests caught:**

- `evidence/capture.py` imported `SurfaceDriver` — a real chokepoint violation, moved to
  `runtime/capture.py`. Import-linter missed it because the module was not yet in the import graph;
  the AST scan in `test_policy_chokepoint.py` did not. Worth noting the reasoning on the fix: a
  carve-out for "read-only driver methods are fine" would have been defensible in isolation and
  would immediately have made the invariant something to reason about case-by-case rather than
  something to check. Moving one module was cheaper than weakening the rule.
- **An allowlist narrowing could be bypassed.** A capability's `allowed_domains` intersected the
  domain set but then fell through to the *global* `url_patterns`, so a capability scoped to one
  host still reached anything a pattern matched. A narrowing a pattern can reopen is not a
  narrowing. Now applied as a separate check after the global one.

**Note on layer 2's strength.** The mint token stops accidents, not a determined caller — Python has
no real private constructor. That limit is stated in `policy/authorized.py` rather than oversold;
the import rule and the authorize↔dispatch reconciliation are what hold against intent.

## Risks

| Risk | Mitigation |
|---|---|
| Redaction that is thorough enough to be useless for debugging | Redact *values*, keep *shapes*: `member_id=<redacted:string[5]>`. A debugger needs to know a field was present and well-formed. |
| The token guard is trivially bypassable in Python | True — it stops accidents, not adversaries. The import rule and the reconciliation test are the real enforcement. Stated as a limit in REPORT.md §6. |

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
