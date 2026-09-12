# Accessibility

Not a separate pass at the end — build it in, because retrofitting focus order and contrast into a
finished page is slower than doing it right the first time. This is the non-negotiable minimum,
checked before anything ships.

## Checklist

- **Contrast.** Body text against its background meets WCAG AA (4.5:1). Large text (≥24px, or ≥19px
  bold) meets 3:1. Check the *actual* token pairs in [design-tokens.md](design-tokens.md), in both
  themes — a pairing that passes in light mode doesn't automatically pass in dark.
- **Focus is always visible.** Every interactive element — button, link, table row acting as a
  button, custom control — has a `:focus-visible` outline distinct from its hover state. Never
  `outline: none` without a replacement. Tab through every new screen start to finish before calling
  it done; if you land somewhere invisible, that's a bug, not an edge case.
- **Real interactive elements.** A clickable row is a `<button>` (or has `role="button"` and
  `tabindex="0"` with Enter/Space handling) — never a `<div onclick>` with no keyboard path and no
  accessible name. A screen reader user should hear what the row *is* ("Open run demo-agent-missing"),
  not just its raw cell text concatenated.
- **Labels, not just placeholders.** Every form control has a `<label>` associated with it (`for`/
  `id`, or wrapping). An icon-only button has an `aria-label` or visible `title`.
- **Motion respects the OS setting.** `prefers-reduced-motion: reduce` zeroes out transitions and
  animations, not just shortens them.
- **Color is never the only signal.** A status pill pairs its color with a label and (where
  practical) a distinct icon/shape — someone with color-vision deficiency reads "Needs human" from
  the text, not from "it's the amber one."
- **Live regions for async updates.** A toast container is `aria-live="polite"` so a screen-reader
  user hears "Session claimed" without needing to be focused on that corner of the screen.
- **Touch targets ≥44×44px** on any control usable on a touch device, with real spacing between
  adjacent targets (not just visual padding that a fat-finger tap can still miss into the wrong
  target).

## How to check it, not just claim it

- Tab through the whole page with a keyboard only, no mouse.
- Toggle the OS's reduced-motion setting and confirm animations actually stop.
- Zoom the page to 200% and confirm nothing clips or overlaps.
- Run it through the browser's own accessibility tree inspector (or an automated pass) and read what
  it actually says a control's name/role is — not what you assume it says. The console's
  intervention rows were caught exactly this way: keyboard-reachable and clickable, but reporting no
  accessible name to the tree, meaning "keyboard support" was real but "screen-reader support" for
  that specific control was not.
