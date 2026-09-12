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

- [x] A discovery run emits a `Capability` that validates and round-trips
- [x] It carries typed inputs, typed outputs, an asserted checkpoint, and a content hash
- [x] It is **human-readable** — a reviewer can tell what it does without the trace
- [x] It contains **no raw CSS selector as a target identity** (hints only, and only durable ones)
- [x] `transcript_ref` points at the discovery evidence rather than inlining it
- [x] **No outcomes or recovery are invented** — see below; the gaps are named instead
- [x] A run that did not reach its goal is refused

### Verification record

189 tests green; `mypy --strict` on 62 files; 5/5 contracts. The compiler tests run the real loop
against the real app and compile what it produced, so the artifact under assertion was genuinely
derived from a working run rather than hand-built to satisfy the compiler.

**The compiler invents no error handling, deliberately.** A successful run never sees a "record not
found" screen, so scaffolding a detector for one would declare error handling that was never
observed to work. Confidently wrong error handling is worse than none: it makes a reviewer believe
the question has been answered. Instead `outcomes` and `recovery` are empty, `provenance.notes`
names exactly what a reviewer must add, and the CLI says so in yellow when the run finishes.

This is a change from the plan, which said the compiler would "scaffold outcomes and recovery for
human review". Scaffolding them from domain knowledge rather than from observation would be
fabrication dressed as a draft.

**Parameter lifting requires two signals**: the value was typed into a field *and* appears in the
goal. Either alone over-lifts — "typed into a field" would lift a fixed dropdown choice; "appears in
the goal" would lift incidental words. A value failing the test stays literal, which is the safe
direction: an over-literal capability is obviously wrong on the second call, while an
over-parameterized one silently accepts an argument it ignores.

**The checkpoint is built from observation, not from the model's hint.** A self-report about a run
that has already ended cannot be a machine-checkable claim about the screen in front of the
executor. The hint is preserved for the reviewer; the assertion asserts the *shape* of the extracted
outputs plus the label beside them — which is what makes it specific to this goal rather than true
of any page.

**Two bugs caught by reading the first compiled artifact rather than by a test:**

- A session-scoped CDP backend node id (`hints.native`) was being persisted. It can never resolve in
  a later session, and it looks like a fast path to anyone reading the YAML.
- A member number was typed as `integer`. Leading zeros are meaningful in banking identifiers, and
  `integer` invites a caller — or a JSON parser on the way in — to drop them. Identifiers are
  strings with a digit pattern.

**Layering change:** `DiscoveryRun`/`DiscoveryStep`/`StopReason` moved from `cua.agent` into
`cua.domain.discovery`. A trace is the output of the probabilistic half and the input to the
compiler, so it belongs to neither — and keeping it in the agent package would have dragged the
model client behind every module that reads one, which matters because the no-LLM-in-replay
invariant is about transitive reach.

## Risks

| Risk | Mitigation |
|---|---|
| Over-generalization: wrong literal becomes a parameter | Conservative lifting rules + draft status + human review gate. Documented as a known limit. |
| Under-specified checkpoints ("page loaded") that pass trivially | Require the checkpoint to assert something *goal-specific* — the extracted output's shape, not just a heading. |
