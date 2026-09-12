# Patterns

Page-level structure: shells, navigation, responsive behavior, and the small handful of recurring
layouts every new screen should reach for before inventing something bespoke.

## Application shell

A tool with more than ~4 views gets a persistent shell: sidebar (product identity top, primary
navigation, secondary items like Settings/About pinned to the bottom) + a top bar (current view
title, live status if the product has one, account/theme controls) + the content area. See
`src/cua/hitl/console/static/index.html` and `app.js` for the reference implementation — hash-based
routing, no build step, one shared shell rendered once and patched, never torn down and rebuilt per
navigation (rebuilding the whole shell on every route change or poll tick is a real bug class: it
destroys focus state and any in-flight animation — see the console's `app.js` `updateSidebarActive`/
`updateSidebarBadges` split for the fix).

A marketing/docs page (`site/`) doesn't need this shell — a simple header + content + footer is
correct there. Match the shell to what the page actually is; don't impose a dashboard chrome on a
single scrolling page, or drop shell navigation on a multi-view tool.

## Responsive rules

Three states, not a continuum you improvise per page:

- **Desktop (≥1024px):** full shell, sidebar with icons + labels.
- **Tablet (768–1024px):** sidebar collapses to icons only (labels hidden, not removed — a tooltip
  or `title` attribute keeps them discoverable).
- **Mobile (<768px):** sidebar becomes an off-canvas drawer, triggered by a hamburger button in the
  top bar, with a backdrop that closes it on tap. **Test this specific breakpoint by actually
  loading the page at a true narrow viewport** (an iframe sized to the target width is a reliable
  way to verify when a preview tool's own viewport emulation is unreliable) — a media query that
  looks right in the CSS source is not the same as confirming it wins the cascade. Two real bugs
  were caught exactly this way while building the console: a later, unconditional rule silently
  outranking an earlier media-query rule at equal specificity, on both `position` and the drawer's
  open-state `transform`.

Every layout tests at 1440, 1280, 1024, 768, and ~380px before being called done — not just resized
by eye, but checked for actual overflow, actual touch-target size (44×44px minimum), and actual
tap-target spacing.

## Live/real-time data

A view that polls for live state (the console's ~2.5s state poll is the reference) patches only what
changed — a status pill's text, a badge's count — never re-renders the whole section on every tick.
Full-section re-renders on a timer are how a page ends up fighting an automated test's element
references, and the same DOM churn costs a keyboard/screen-reader user their place on the page.

## Confirmation

Any destructive or hard-to-reverse action (release a live session, delete, discard unsaved work)
gets a dialog stating the specific consequence in one sentence — not a generic "Are you sure?" — and
a button labeled with the actual verb ("Release", "Delete"), never a bare "OK".

## Settings

Split visibly into what's actually interactive (a real, working, client-side preference — theme,
density) and what's a read-only mirror of server/config state. Label the read-only sections as such,
in one sentence, naming *where* the real value is set (a config file path, an environment variable)
— never render config values inside `<input>`/`<select>` elements that look editable and aren't
(principles.md rule 4). The console's Settings page is the reference for this split.

## Data/evidence browsers

For a view whose job is showing structured data a machine produced (JSON traces, snapshots, logs):
render it with real indentation and light syntax coloring (see `util.js`'s `jsonView`), inside a
monospace panel with its own scroll — never a raw, unformatted dump, and never truncated without
saying so. A list-then-detail layout (narrow list on the left, detail on the right, stacking on
narrow screens) is the default shape for "browse many records, inspect one closely."
