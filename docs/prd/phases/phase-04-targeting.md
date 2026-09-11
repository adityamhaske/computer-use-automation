# Phase 04 — Semantic targeting & resolution ladder ⭐

**Objective.** Resolve a `TargetDescriptor` against a `UiSnapshot`, deterministically, or refuse.

Reference: [design/target-resolution.md](../../design/target-resolution.md).

## Scope

- `targeting/resolver.py` — the seven-rung ladder, tried in fixed order
- `targeting/scoring.py` — **pure** scoring functions; ties broken by snapshot document order
- `targeting/ambiguity.py` — threshold logic and the refusal path
- `targeting/drift.py` — `recorded_strategy` vs `strategy_used`, fingerprint comparison, `drift_score`

The seven rungs: `semantic_exact`, `semantic_normalized`, `label_association`, `structural_anchor`,
`hint_cached`, `ordinal_in_region`, `vision` *(discovery only)*.

Two rules that carry disproportionate weight:

- **`hint_cached` is a cache, not an identity.** A hint is accepted only if the node it finds also
  satisfies the semantic assertion. Otherwise it is discarded and the ladder continues.
- **Ambiguity is refusal.** Two surviving candidates ⇒ `TARGET_AMBIGUOUS`, never a pick. In a bank,
  two plausible Submit buttons means the screen is not what we think it is.

## Non-scope

Executing anything. The resolver returns a node or refuses; dispatch is Phase 05.

## Exit criteria

- [ ] Each rung unit-tested in isolation, and the ladder's *order* tested
- [ ] Ambiguous descriptor raises `TARGET_AMBIGUOUS` — asserted it does **not** pick the first
- [ ] Scoring is pure: same inputs ⇒ same output, no clock, no set iteration
- [ ] `vision` is unreachable when a replay context is active
- [ ] Drift detected between base and Variant B snapshots
- [ ] **No `playwright` or `cua.surfaces` import in `targeting/`**

## Risks

| Risk | Mitigation |
|---|---|
| Ambiguity threshold too tight (constant refusals) or too loose (wrong clicks) | Tune against the mock app; the `replay_stability` eval reports refusal rate and wrong-action rate separately. Bias toward refusal — it is the recoverable failure. |
| `structural_anchor` over-fits our table layout | Express it as a general relation (`row_of`, `labelled_by_adjacent`, `within_section`), not as a CSS path. |
