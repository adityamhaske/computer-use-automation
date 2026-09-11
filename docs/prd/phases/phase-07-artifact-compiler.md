# Phase 07 — Artifact compiler (trace → capability) ⭐

**Objective.** Turn a successful discovery trace into a reviewable, immutable `Capability`.

**This is where "the model discovers, the artifact becomes a capability" actually happens.**

## Scope

| Module | Responsibility |
|---|---|
| `recorder/compile.py` | Trace → `Capability` |
| `recorder/generalize.py` | Lift literals to typed parameters (`"12345"` → `{$input: member_id}`) |
| `recorder/descriptors.py` | Synthesize a `TargetDescriptor` from the resolution that *actually won*, including alternates for lower rungs |
| `recorder/checkpoint.py` | Infer a checkpoint from the terminal state the goal reached |
| `recorder/scaffold.py` | Draft `outcomes` and `recovery` entries **for human review** |

## Design notes

- Descriptor synthesis records `recorded_strategy` — the drift baseline. It also records anchors and
  ordinals *even when `semantic_exact` won*, so the ladder has rungs to fall back to later.
- Parameter lifting is the riskiest inference: lifting the wrong literal produces a capability that
  looks right and is wrong. Mitigations: lift only values that appeared in the goal or in a typed
  input position, and mark everything else literal.
- **Compiler output is explicitly a draft.** `CapabilityApproval` starts at `draft`. The
  `draft → approved` gate exists precisely because this step is heuristic.

## Exit criteria

- [ ] A discovery run emits a `Capability` that validates and round-trips
- [ ] It carries typed inputs, typed outputs, an asserted checkpoint, and a content hash
- [ ] It is **human-readable** — a reviewer can tell what it does without the trace
- [ ] It contains **no raw CSS selector as a target identity** (hints only)
- [ ] `transcript_ref` points at the discovery evidence rather than inlining it
- [ ] Scaffolded outcomes/recovery are marked as drafts, not asserted truths

## Risks

| Risk | Mitigation |
|---|---|
| Over-generalization: wrong literal becomes a parameter | Conservative lifting rules + draft status + human review gate. Documented as a known limit. |
| Under-specified checkpoints ("page loaded") that pass trivially | Require the checkpoint to assert something *goal-specific* — the extracted output's shape, not just a heading. |
