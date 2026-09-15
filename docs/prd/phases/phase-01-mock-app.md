# Phase 01 — Hostile-but-bounded mock back-office app

**Objective.** A local stand-in for a credit union back-office system that is hostile enough to
defeat naive CSS-selector automation and honest enough to debug.

**Why we build rather than borrow.** A public demo site cannot inject a session timeout on demand,
cannot produce a permission-denied path, and cannot be forked into a second tenant. The brief
requires evidence of a replay hitting an exceptional state — that requires a target we control.

## Scope

**The flow** (search → detail → action, plus a multi-field form with confirmation):
login → member search → member detail (savings balance) → open sub-account → confirmation.

**Hostility, on purpose:**
- frameset shell with named frames (nav / content)
- table-based layout, deeply nested
- non-semantic markup, inline `onclick`, server-rendered
- **no test IDs anywhere**
- labels that sit in an adjacent `<td>` rather than a `<label>` — so `structural_anchor` earns its place

**Boundedness, equally on purpose:**
- deterministic by construction; seeded data; no randomness outside explicit fault flags
- no timing races; every state reachable by URL for test setup

**Faults** (flag-gated, deterministic). Build these four first:
`record_not_found`, `transient_load` (502, N times then succeed), `session_timeout`,
`undeclared_dialog` (an interstitial no capability declares → must produce `UNEXPECTED_STATE`).
Then, only if cheap: `validation_error`, `permission_denied`.

**Variant B** — the "second tenant" running the same vendor product. Two distinct divergences:

- *Case A — markup churn:* the member-number field keeps its label but changes CSS class and form
  field name. The ladder absorbs this automatically → the falsifiable form of the no-CSS claim.
- *Case B — rebranding:* the savings-balance and account-type labels change. This defeats
  `structural_anchor` too (the anchor text is the label), so it fails closed and needs a four-line
  `TenantBinding` overlay.

Case B is the one that justifies the overlay design. Making the ladder appear to absorb rebranding
would be making it guess.

## Non-scope

Real auth, a database, styling beyond what hostility requires, more screens than the flow needs.

## Exit criteria

- [x] Boots via `make app`; Variant B via `make app-variant-b`
- [x] Every fault reproducible by flag, identically, every time
- [x] Zero nondeterminism outside fault flags (asserted: same request → byte-identical HTML)
- [x] Variant B demonstrates both divergence cases (markup churn / rebranding)
- [x] No real PII; credentials are obvious fakes
- [x] Every business outcome has a member that produces it

### Verification record

27 tests green (`tests/integration/test_mock_app.py`), covering the full flow, all four faults,
every business outcome, byte-level determinism, and both Variant B cases.

The Phase 03 semantic-tree spike was pulled forward and run against this app —
`scripts/spike_semantic_tree.py`, results in
[phase-03](phase-03-perception-driver.md). It confirmed the app is hostile in the way intended
(page-level AX tree sees only opaque frames) and tractable in the way required (per-frame trees
expose `row → label cell → value cell`). It also disproved a design claim, which reshaped Variant B.

Live confirmation of case A, from the spike output — Variant B's field has a different form name
(`member_num`) and CSS class (`ng-inp`), and still resolves:

```
VARIANT B  2. Per-frame getFullAXTree ...... 45 nodes in 'content'
              textbox        name='Member Number'
              button         name='Search'
```

## Risks

| Risk | Mitigation |
|---|---|
| Over-building the mock instead of the system | Time-box. It is a fixture, not a deliverable. Five screens, four faults. |
| So hostile the semantic tree is useless | Phase 03 spike measures this immediately; the driver may enrich nodes from table headers. |

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
