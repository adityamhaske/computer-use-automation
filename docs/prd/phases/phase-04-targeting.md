# Phase 04 — Semantic targeting & resolution ladder ⭐

**Objective.** Resolve a `TargetDescriptor` against a `UiSnapshot`, deterministically, or refuse.

Reference: [design/target-resolution.md](../../design/target-resolution.md).

## Scope

- `targeting/resolver.py` — the seven-rung ladder, tried in fixed order
- `targeting/scoring.py` — **pure** scoring functions; ties broken by snapshot document order
- `targeting/ambiguity.py` — threshold logic and the refusal path
- `targeting/drift.py` — `recorded_strategy` vs `strategy_used`, fingerprint comparison, `drift_score`

The rungs: `semantic_exact`, `semantic_normalized`, `structural_anchor`, `hint_cached`,
`ordinal_in_region`, `vision` *(discovery only)*.

Two rules that carry disproportionate weight:

- **`hint_cached` is a cache, not an identity.** A hint is accepted only if the node it finds also
  satisfies the semantic assertion. Otherwise it is discarded and the ladder continues.
- **Ambiguity is refusal.** Two surviving candidates ⇒ `TARGET_AMBIGUOUS`, never a pick. In a bank,
  two plausible Submit buttons means the screen is not what we think it is.

## Non-scope

Executing anything. The resolver returns a node or refuses; dispatch is Phase 05.

## Exit criteria

- [x] Each rung unit-tested in isolation, and the ladder's *order* tested
- [x] Ambiguous descriptor refuses — asserted it does **not** pick the first
- [x] Scoring is pure: same inputs ⇒ same output, no clock, no set iteration
- [x] `vision` is unreachable when a replay context is active
- [x] Drift detected between base and Variant B
- [x] **No `playwright` or `cua.surfaces` import in `targeting/`**
- [x] Cross-tenant: case A resolves with no overlay, case B fails closed then resolves with one

### Verification record

114 tests green (16 resolver unit tests, 6 cross-tenant browser tests). `mypy --strict` on 40 files;
5/5 contracts.

**There is no ambiguity threshold, deliberately.** An earlier design had one. A tunable threshold
would inevitably be turned up until things "worked", which is exactly the failure this resolver
exists to prevent. Any unresolved multiplicity is a refusal; the reported `ambiguity` figure is for
debugging, not for tuning. The only disambiguator is an explicit `ordinal` the artifact's author
supplied on purpose.

**`label_association` was removed from the ladder** — it could never fire. See
[design/target-resolution.md](../../design/target-resolution.md).

**Two bugs the cross-tenant test caught, both in the reference artifact rather than the resolver:**

- Its entrypoint was `{base_url}/search`, the bare form. But this app is only ever used through its
  frameset, which is why every descriptor scopes to `frame: content` — so the recorded scopes could
  never have matched the page the entrypoint loads. Corrected to the frameset shell.
- Its cached `hints.css` was hand-written as `input.frmfld`, while the driver actually emits
  `input[name="memno"]`. A fixture that does not match what the compiler produces is a fixture that
  tests nothing.

## Risks

| Risk | Mitigation |
|---|---|
| `structural_anchor` over-fits our table layout | Expressed as general relations (`row_of`, `adjacent_to`, `within_section`), never as a path — so they mean the same thing on a desktop surface. |
| Refusal rate too high to be usable | Measured separately from wrong-action rate by the `replay_stability` eval. They are never traded off: a refusal costs minutes, a wrong click on a financial screen may not be recoverable. |
