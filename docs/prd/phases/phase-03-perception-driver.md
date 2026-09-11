# Phase 03 — Perception & surface driver

**Objective.** Turn a live surface into a normalized `UiSnapshot`, behind a port that a desktop
driver could also implement.

Reference: [ADR 0001](../../adr/0001-uisnapshot-as-the-cross-surface-abstraction.md).

## Spike first

**Before anything else**, measure semantic-tree quality on the Phase 01 frameset/table app:

- Do controls carry usable roles and accessible names?
- Do labels in adjacent `<td>`s associate at all?
- Are frames traversable in one snapshot?

This is the load-bearing assumption of the whole design. If it fails, the fallback is *driver-side
enrichment* (infer names from table headers, adjacent cells, and headings) — which changes the
driver's internals and nothing above it. **The spike result goes in the phase log and into REPORT.md
§4 either way**, because "we measured this" is a better answer than "we assumed this."

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

- [ ] `observe()` returns a useful snapshot of every mock-app screen, including inside frames
- [ ] Two consecutive snapshots of a static page are **identical** (stable `node_id`s)
- [ ] **No AX/CDP/Playwright type escapes `surfaces/`** — `.importlinter` `surface-neutral-targeting`
- [ ] Snapshot of Variant B differs from base in names but not in structure
- [ ] Spike findings written up

## Risks

| Risk | Mitigation |
|---|---|
| **Thin accessibility tree on the hostile app** — the project's biggest technical bet | The spike runs first, before Phase 04 depends on it. Fallback is driver-side enrichment, which leaves the architecture intact. |
| Unstable `node_id`s break determinism | Derive them from structural position + role + name, never from a counter or memory address. Asserted by the identical-snapshot test. |
