# ADR 0001 — `UiSnapshot` is the cross-surface abstraction

- **Status:** Accepted
- **Date:** 2026-09-11
- **Context:** Brief §3.1 ("bias toward an approach that would still work when the surface has no
  clean DOM") and §3.7 (extend to legacy web and desktop)

## Context

We must perceive and act on three very different kinds of surface: modern web apps, legacy
server-rendered web apps (framesets, nested tables, non-semantic markup, no test IDs), and native
desktop applications. We implement against one of them, but the abstractions must not paint us into
a corner.

Three candidate perception strategies:

1. **DOM/CSS first** — easiest and most reliable on a cooperative web app. But the brief says the
   common case is *no clean DOM*, and a DOM does not exist on a desktop app at all.
2. **Screenshot + coordinates (pure vision)** — maximally general; it's what "computer use" means
   literally. But pixel coordinates are the least stable thing you can persist in a replayable
   artifact: they break on viewport size, zoom, font settings, and scroll position.
3. **Semantic/accessibility tree** — browsers expose one; Windows exposes UIA; macOS exposes the
   Accessibility API. It carries role, name, value, and state — the same vocabulary a human operator
   uses ("the *Search* button").

## Decision

**Perception normalizes every surface into a `UiSnapshot`: a tree of `UiNode {node_id, role, name,
value, states, bounds, scope, parent, hints}`.**

`UiSnapshot` — not the accessibility tree — is the architectural commitment. A driver populates it
from whatever its surface offers:

| Driver | Populates `UiSnapshot` from |
|---|---|
| `PlaywrightCdpDriver` (built) | CDP `Accessibility.getFullAXTree` + box model, enriched with DOM-derived hints |
| `DesktopUiaDriver` (stubbed) | Windows UI Automation element tree |
| A future OCR driver | Vision model + OCR over a screenshot |

The artifact schema and the targeting system depend **only** on `UiSnapshot` and
`TargetDescriptor`. Neither knows what an accessibility tree, a CDP session, or a DOM is.
Surface-specific data lives exclusively in `TargetDescriptor.hints`, which is an unverified cache.

## Consequences

**Good.** The same artifact can, in principle, replay on a desktop driver, because
`role: button, name: "Search"` means the same thing in UIA as in ARIA. The seam between "how we
perceive a surface" and "the recorded flow" — which §3.7 asks us to name — is exactly the
`SurfaceDriver` port. It is enforced, not aspirational: `.importlinter` forbids `cua.targeting` and
`cua.perception` from importing `playwright` or `cua.surfaces`.

**Bad.** Accessibility trees on genuinely hostile legacy apps can be thin — a `<td>` full of text may
surface as a generic node with no useful name. We accept this and mitigate it two ways: the driver
may *enrich* nodes using DOM-derived label/heading/table-header inference (still behind the port,
so nothing above is affected), and the resolution ladder includes `structural_anchor`, which
identifies a control by its position relative to nearby text rather than by its own name.

**Risk accepted.** If the semantic tree turns out to be unusable on our own hostile app, the
mitigation above is the fallback rather than a redesign — the abstraction does not change, only the
driver's internals. Phase 03 spikes this before Phase 04 depends on it.

## Rejected

- **Pure vision** — rejected as the *primary* strategy for determinism reasons above. Retained as
  strategy 7 of the ladder, and hard-disabled during replay.
- **DOM/CSS first** — rejected as primary. Retained as a cached *hint* (strategy 5), and only ever
  accepted when the node it finds also satisfies the semantic assertion.

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
