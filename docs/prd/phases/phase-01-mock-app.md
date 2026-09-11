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

**Variant B** — the "second tenant" running the same vendor product:
- relabels controls (`"Member Number"` → `"Member #"`) — defeats exact *and* normalized matching
- restyles markup — invalidates every cached CSS hint
- **preserves row structure** — so `structural_anchor` still resolves

That combination is what makes the no-CSS claim testable rather than rhetorical.

## Non-scope

Real auth, a database, styling beyond what hostility requires, more screens than the flow needs.

## Exit criteria

- [ ] Boots via `make app`; Variant B via `make app-variant-b`
- [ ] Every fault reproducible by flag, identically, every time
- [ ] Zero nondeterminism outside fault flags (asserted: same request → same HTML)
- [ ] Variant B breaks exact-name matching **and** CSS hints, but not row structure
- [ ] No real PII; credentials are obvious fakes

## Risks

| Risk | Mitigation |
|---|---|
| Over-building the mock instead of the system | Time-box. It is a fixture, not a deliverable. Five screens, four faults. |
| So hostile the semantic tree is useless | Phase 03 spike measures this immediately; the driver may enrich nodes from table headers. |
