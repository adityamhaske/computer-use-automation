# Phase 03 — Perception & surface driver

**Objective.** Turn a live surface into a normalized `UiSnapshot`, behind a port that a desktop
driver could also implement.

Reference: [ADR 0001](../../adr/0001-uisnapshot-as-the-cross-surface-abstraction.md).

## Spike — RUN EARLY, results below

Run during Phase 01 rather than at the start of Phase 03, because it is the load-bearing assumption
of the whole design and the cost of being wrong compounds with every phase that builds on it.

### Result 1 — frame traversal needs an explicit frame id ⚠️

`Accessibility.getFullAXTree` at page level on a frameset returns **3 nodes**: the root and two
opaque `Iframe` entries. It does *not* descend into frames.

```
RootWebArea  name='MemberDesk'
Iframe       name=''
Iframe       name=''
```

The fix: enumerate frames via `Page.getFrameTree`, then call
`Accessibility.getFullAXTree({"frameId": ...})` per frame and stitch the results into one
`UiSnapshot`. Confirmed working.

Two mechanisms that do **not** work, recorded so nobody retries them:
- `BrowserContext.new_cdp_session(frame)` → *"This frame does not have a separate CDP session, it is
  part of the parent frame's session."* Same-process frames have no session of their own.
- Page-level `getFullAXTree` with a depth argument — frames are a boundary, not a depth limit.

### Result 2 — the semantic tree is rich enough ✅

Inside a frame, on deliberately hostile table markup, the tree carries exactly what the design
needs:

```
row "Member Number":
  cell "Member Number"
  cell:
    textbox "Member Number"      <- from a title attribute, as legacy apps commonly have

row "Savings Balance $4,210.55":
  cell "Savings Balance"
  cell "$4,210.55"               <- adjacent-cell extraction is directly implementable
```

So `semantic_exact` is viable (`textbox`/`button` carry real accessible names), and
`structural_anchor` is viable (row → label cell → value cell is explicit in the tree). No
driver-side enrichment fallback is needed. The AX-first bet holds.

### Result 3 — a design claim was wrong, and the spike caught it ⚠️

An earlier draft asserted that `structural_anchor` survives tenant rebranding. It does not: Variant
B relabels the *label cell* along with the field, so the anchor text changes too and the anchor has
nothing stable to hold.

This reshaped Variant B into two distinct cases — markup churn (ladder absorbs it) and rebranding
(fails closed, needs a `TenantBinding` overlay) — and corrected
[design/target-resolution.md](../../design/target-resolution.md) and
[ADR 0005](../../adr/0005-tenant-overlay-not-fork.md). The corrected position is the stronger one:
detect → refuse → cheap override, rather than a system that guesses "Savings Bal." means "Savings
Balance".

Ten minutes in a spike; it would have been Phase 08 otherwise.

### Consequences for this phase

- The driver must stitch per-frame AX trees into one `UiSnapshot`, with `scope.frame` recording
  which frame a node came from.
- `node_id` must be stable across snapshots and unique across frames.
- No enrichment fallback required — but keep it available behind the port if a future surface needs it.

## Scope

- `surfaces/base.py` — the `SurfaceDriver` protocol: `observe() -> UiSnapshot`,
  `dispatch(AuthorizedAction) -> ActionResult`, `screenshot()`, `session_info()`
- `surfaces/playwright_cdp/` — CDP accessibility tree + box model → `UiNode`s; frame traversal;
  screenshots; optional DOM-derived enrichment
- `perception/normalize.py` — role/name normalization, scope inference, stable `node_id`s
- `perception/fingerprint.py` — per-node fingerprint for drift detection
- `surfaces/desktop_uia/` — **Protocol + docstring only.** Maps UIA `ControlType`→role,
  `Name`→name, `LegacyIAccessible`→value. Zero implementation. If it grows, stop.

## Exit criteria

- [x] `observe()` returns a useful snapshot of every mock-app screen, including inside frames
- [x] Two consecutive snapshots of a static page are **identical** (stable `node_id`s and
      fingerprints)
- [x] **No AX/CDP/Playwright type escapes `surfaces/`** — `.importlinter` `surface-neutral-targeting`
      passes with real imports present (111 dependencies analyzed)
- [x] Variant B: semantics survive, every cached selector dies (case A measured on a real browser)
- [x] Controls with **no accessible name at all** are still perceived and anchorable
- [x] Spike findings written up (above)

### Verification record

92 tests green, 9 of them browser-backed against the real frameset app. `mypy --strict` clean across
38 files; 5/5 contracts.

**A real bug the browser test caught.** `DOM.getAttributes` takes a *frontend* `nodeId`, not the
`backendNodeId` the accessibility tree provides — so every attribute fetch was throwing, and a
`try/except` intended for detached nodes swallowed it silently. The failure mode was simply "no
hints", which is indistinguishable from a page whose controls carry no cacheable attributes. Fixed
by using `DOM.describeNode`.

Worth noting *why* it was caught: the Variant B test asserts a specific cached selector
(`input[name="member_num"]`) rather than merely asserting that resolution succeeds. A weaker
assertion would have passed, and the CSS-invalidation demo in Phase 11 would have been silently
vacuous — proving nothing, because there would have been no hints to invalidate.

**Design notes:**

- `observe()` deliberately does **not** fetch geometry. Bounds cost a CDP round trip per node,
  `observe()` runs on every step of every run, and almost nothing needs them. `bounds_for()` fetches
  them when screenshot redaction or a vision fallback actually does.
- Actions dispatch against the node that was *perceived*, via its backend node id — never by
  re-querying with a selector. Re-querying would reintroduce the brittleness the targeting design
  exists to avoid, and opens a window where the page changes between resolution and action.
- The snapshot builder was rewritten mid-phase to drop an O(n²) reverse lookup. On a 601-element
  page it now builds in ~9ms; the quadratic version would have degraded on exactly the deeply-nested
  pages this project targets.

## Risks

| Risk | Mitigation |
|---|---|
| **Thin accessibility tree on the hostile app** — the project's biggest technical bet | The spike runs first, before Phase 04 depends on it. Fallback is driver-side enrichment, which leaves the architecture intact. |
| Unstable `node_id`s break determinism | Derive them from structural position + role + name, never from a counter or memory address. Asserted by the identical-snapshot test. |
