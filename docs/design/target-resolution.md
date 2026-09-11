# Design — target resolution

> Decision rationale lives in [ADR 0001](../adr/0001-uisnapshot-as-the-cross-surface-abstraction.md).

Resolution is the act of turning a `TargetDescriptor` (semantic, stored in the artifact) into a
concrete `UiNode` (specific, present in the current `UiSnapshot`). It is where determinism is won or
lost.

## The ladder

Strategies are tried in this **fixed order**. The first that yields exactly one candidate wins.

| # | Strategy | Matches on | Survives |
|---|---|---|---|
| 1 | `semantic_exact` | role + accessible name, exact, unique in scope | most things |
| 2 | `semantic_normalized` | as above, case/whitespace/punctuation folded | cosmetic label edits |
| 3 | `label_association` | control ↔ its associated label | unnamed inputs |
| 4 | `structural_anchor` | position relative to nearby text — *"the textbox in the row whose first cell reads 'Member Number'"* | **rebranding, and controls with no usable name at all** |
| 5 | `hint_cached` | the stored CSS/node-path hint | nothing structural; it is a speed optimization |
| 6 | `ordinal_in_region` | Nth control of a role within a scope | last resort before refusal |
| 7 | `vision` | OCR / bounding box | **discovery only — hard-disabled in replay** |

Strategy 4 is the one that makes legacy enterprise apps tractable. Those apps are full of controls
with no name, no label, and no test ID, sitting in a table cell next to the text that identifies
them. A human reads the row; so does this.

Strategy 5 is a **cache, not an identity**. A hint is accepted only if the node it resolves to
*also* satisfies the semantic assertion. If the CSS still matches but the role or name has changed,
the hint is discarded and the ladder continues. This is what stops the artifact from silently
becoming CSS-coupled.

## Determinism rules

1. Ladder order is fixed and configuration-frozen into the `RunRecord`.
2. Within a strategy, candidates are scored by a **pure** function of `(descriptor, node, snapshot)`.
3. Ties break by **snapshot document order** — never by set/dict iteration order, never by a clock,
   never randomly.
4. `vision` is unreachable in replay (asserted by test, not merely by configuration).
5. Same artifact + same inputs + same app state ⇒ byte-identical decision trace.

## Ambiguity is refusal, not a tiebreak

If two or more candidates survive above the ambiguity threshold, the resolver raises
`TARGET_AMBIGUOUS` and the run stops. It does **not** pick the first, the topmost, or the
highest-scoring.

This is a deliberate trade against convenience. In a back-office banking application, two plausible
"Submit" buttons mean the screen is not what we think it is, and acting on either is how an
automation posts a transaction to the wrong account. A refusal is recoverable — it escalates to a
human with full context. A wrong click may not be.

The corresponding eval metric is **wrong-action rate, target zero**. A high refusal rate is a
quality problem; a non-zero wrong-action rate is a safety incident.

## Drift

Each resolution records:

```python
{ strategy_recorded, strategy_used, candidates_considered, score, ambiguity, fingerprint_match }
```

Resolving via a *lower-priority* strategy than the one recorded at discovery is **drift** — a
signal, not a silent success. Per-step drift aggregates into a run-level `drift_score`.

This is the mechanism behind the multi-tenant story: a tenant whose UI has diverged shows rising
drift *before* replays begin to fail, which is the window in which a re-record or an overlay is
cheap.

## Worked example: Variant B

Variant B of the mock app stands in for a second credit union running the same vendor product. It:

- relabels `"Member Number"` → `"Member #"` — defeats `semantic_exact` **and** `semantic_normalized`
  (these are different words, not different whitespace)
- restyles the markup entirely — invalidates every cached CSS `hint`
- **preserves the row structure** — the field is still the input in the row labelled by that cell

So the ladder descends to `structural_anchor`, the run succeeds, and `drift_score > 0`. That
sequence is asserted in `tests/integration/test_variant_b.py` and measured by the `cross_tenant`
eval.

It is the concrete proof of the claim that would otherwise be hand-waving: **this system does not
depend on CSS selectors.** If it did, Variant B would fail.
