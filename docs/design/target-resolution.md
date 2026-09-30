# Design — target resolution

> Decision rationale lives in [ADR 0001](../adr/0001-uisnapshot-as-the-cross-surface-abstraction.md).

Resolution is the act of turning a `TargetDescriptor` (semantic, stored in the artifact) into a
concrete `UiNode` (specific, present in the current `UiSnapshot`). It is where determinism is won or
lost.

## The ladder

Strategies are tried in this **fixed order**. The first rung that yields any candidate decides:
exactly one wins; more than one is refused (below) unless the descriptor carries an explicit
`ordinal`. A rung with several candidates does not fall through to the next.

| # | Strategy | Matches on | Survives |
|---|---|---|---|
| 1 | `semantic_exact` | role + accessible name, exact, unique in scope | most things |
| 2 | `semantic_normalized` | as above, case/whitespace/punctuation folded | cosmetic label edits |
| 3 | `structural_anchor` | position relative to nearby text — *"the textbox in the row whose first cell reads 'Member Number'"* | **controls with no usable name at all** |
| 4 | `hint_cached` | the stored CSS/node-path hint | nothing structural; it is a speed optimization |
| 5 | `ordinal_in_region` | Nth control of a role within a scope | rebranding; last resort before refusal |
| 6 | `vision` | OCR / bounding box | **not implemented** — no generator exists; replay excludes it by construction |

Rung 3 is the one that makes legacy enterprise apps tractable. Those apps are full of controls with
no name, no label, and no test ID, sitting in a table cell next to the text that identifies them. A
human reads the row; so does this.

Rung 4 is a **cache, not an identity**. A hint is accepted only if the node it resolves to *also*
satisfies the semantic assertion. If the CSS still matches but the role or name has changed, the
hint is discarded and the ladder continues. This is what stops the artifact from silently becoming
CSS-coupled.

### A rung that was removed

An earlier draft carried `label_association` between rungs 2 and 3. Implementing the resolver showed
it could never fire: every surface this design targets — ARIA, UIA, macOS AX — already folds label
association into the computed accessible name. A control *with* an associated label is therefore
found by `semantic_exact`, and one *without* is found by `structural_anchor`. There is no case in
between.

A rung that cannot fire is worse than no rung. It makes the ladder look more capable than it is, and
it invites someone to "fix" a resolution problem by adding logic to a dead path. A driver on a
surface that genuinely exposes labelling separately (UIA's `LabeledBy` is a distinct property from
`Name`) should fold it into the node's name during normalization, where the rest of the system
already handles it.

## Determinism rules

1. Ladder order is fixed and configuration-frozen into the `RunRecord`.
2. Within a strategy, candidates are *filtered*, not scored, by a **pure** function of
   `(descriptor, snapshot)`, and returned in **snapshot document order** — never set/dict iteration
   order, never a clock, never randomly.
3. There is no tie-break. Several survivors are a refusal; the one exception is an explicit
   `ordinal` in the descriptor, which indexes into that document order.
4. `vision` is unreachable in replay (asserted by test, not merely by configuration).
5. Same artifact + same inputs + same app state ⇒ byte-identical decision trace.

## Ambiguity is refusal, not a tiebreak

If a rung yields two or more candidates and the descriptor carries no explicit `ordinal`, the
resolver raises `TARGET_AMBIGUOUS` and the run stops. It does **not** pick the first, the topmost, or
the highest-scoring. There is no threshold to tune: the reported `ambiguity` figure,
`(candidates − 1) / candidates`, is for debugging and is never compared against anything.

This is a deliberate trade against convenience. In a back-office banking application, two plausible
"Submit" buttons mean the screen is not what we think it is, and acting on either is how an
automation posts a transaction to the wrong account. A refusal is recoverable — it escalates to a
human with full context. A wrong click may not be.

The corresponding eval metric is **wrong-action rate, target zero**. A high refusal rate is a
quality problem; a non-zero wrong-action rate is a safety incident.

## Drift

Each resolution records:

```python
{ strategy_recorded, strategy_used, candidates_considered, ambiguity, fingerprint_match }
```

Resolving via a *lower-priority* strategy than the one recorded at discovery is **drift** — a
signal, not a silent success. Per-step drift aggregates into a run-level `drift_score`.

This is the mechanism behind the multi-tenant story: a tenant whose UI has diverged shows rising
drift *before* replays begin to fail, which is the window in which a re-record or an overlay is
cheap.

## Worked example: Variant B

Variant B of the mock app stands in for a second credit union running the same vendor product. It
contains **two different kinds of divergence**, which need two different answers. Conflating them
produces a system that guesses.

### Case A — markup churn, same vocabulary (the ladder handles this)

The member-number field keeps its label, and changes everything a selector could have cached:

| | base | variant B |
|---|---|---|
| accessible name | `Member Number` | `Member Number` — **same** |
| form field name | `memno` | `member_num` — changed |
| CSS class | `frmfld` | `ng-inp` — changed |

`semantic_exact` resolves it on both. The cached `hint.css` is invalidated and discarded, and
nothing breaks. **This is the falsifiable form of the claim "this system does not depend on CSS
selectors":** if it did, Variant B would fail here. It does not.

### Case B — rebranding, different vocabulary (this needs an overlay)

The savings-balance row and the account-type control are relabeled:

| | base | variant B |
|---|---|---|
| balance label | `Savings Balance` | `Savings Bal.` |
| account type | `Account Type` | `Type of Account` |

Note carefully what this defeats. Not only `semantic_exact` and `semantic_normalized` — but
**`structural_anchor` as well**, because the anchor text *is* the label, and the label changed.

There is no automatic recovery here, and that is correct. Inferring that "Savings Bal." means
"Savings Balance" is a fuzzy guess, and a system that guesses which row holds a balance will
eventually read the wrong one. So the designed behaviour is:

1. Resolution fails → `TARGET_NOT_FOUND`, naming the step and the rungs it tried (there is no
   drift score for it: drift is measured on resolutions that succeed)
2. A four-line `TenantBinding` overlay supplies the new label ([ADR 0005](../adr/0005-tenant-overlay-not-fork.md))
3. The replay succeeds

**Detect → refuse → cheap override.** That is the multi-tenant story, and it is a stronger claim
than pretending the ladder absorbs rebranding silently.

### Why this is written down

An earlier draft of this design asserted that `structural_anchor` survives rebranding. Spiking the
accessibility tree against the real app before building the resolver disproved it in about ten
minutes: Variant B's label *cell* is relabeled along with the field, so the anchor has nothing
stable to anchor to. Cheaper to find in a spike than in Phase 08.

`tests/integration/test_variant_b.py` asserts case A succeeds without an overlay and case B fails
closed without one and succeeds with one. The `cross_tenant` eval measures both.

---

[Repository](https://github.com/adityamhaske/computer-use-automation) · [Documentation](https://adityamhaske.github.io/computer-use-automation/) · [Design write-up](https://adityamhaske.github.io/computer-use-automation/report/)
