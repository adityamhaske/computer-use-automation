# Workflow

The process: where files live, how to add or change something, and the checklist to run before
calling it done.

## Where things live

```
src/cua/hitl/console/static/     the operator console (app shell, pages, styles)
  styles/tokens.css                design tokens — edit here to change color/type/space globally
  styles/base.css                  reset + element defaults
  styles/components.css            the shared component library — buttons, cards, tables, ...
  styles/pages.css                 page-level layout only (grids, split-views) — no colors here
  app.js                           shell: router, live-poll store, theme, toasts, confirm dialog
  api.js / ws.js / util.js / icons.js   shared, import directly from any page
  pages/*.js                       one file per route; each exports render(container, ctx)

site/                              the marketing/docs page — a single static page, its own tokens

docs/UIUX/                         this folder
```

No build step anywhere in this repo's web surfaces — plain ES modules loaded via `<script
type="module">`, plain CSS files loaded via `<link>`. Keep it that way unless a genuinely new
requirement forces a bundler; it's a deliberate simplicity, not an oversight (see the top-level
`AGENTS.md` scope-discipline section — the same "don't add infrastructure that doesn't pay for
itself" argument applies to the frontend).

## How to fix an existing UI bug

1. Reproduce it in a real, running instance — not by reading the code and guessing. Start the
   relevant surface (`./start.sh ui` for the console, or open `site/index.html` directly) and drive
   it the way the bug report describes.
2. Isolate whether it's a **markup**, **CSS**, or **state/logic** bug before touching anything —
   inspect the live DOM and computed styles (`getComputedStyle`), not just the source file. Two
   real bugs in this codebase looked like markup problems and were actually CSS cascade-order
   problems (a later, unconditional rule beating an earlier, conditional one at equal specificity) —
   confirmed only by reading the *matched* rules, not by re-reading the source top to bottom.
3. Fix at the root cause, in the shared file if the bug is systemic (a token, a component class) —
   not with a one-off inline `style=""` patch on the single page where it was noticed. An inline
   style is acceptable only for a genuinely one-off accent that will never recur elsewhere.
4. Re-verify in the browser, in both themes, at both a wide and a narrow viewport.

## How to add a new page (console-style app)

1. Add the route + nav entry in `app.js` (`PRIMARY_NAV`/`FOOTER_NAV`, the `PAGES` map, `TITLES`).
2. Create `pages/<name>.js` exporting `render(container, ctx)` (see any existing page for the exact
   contract: what `ctx` provides, when to return a cleanup function).
3. Build the page **only** out of existing classes from `components.css`/`pages.css`. Needing a new
   layout primitive is a signal to add it to `pages.css` as a reusable, named pattern — not to reach
   for an inline `<style>` block on one page.
4. Design all four states (populated, loading, empty, error) before considering it done — see
   [principles.md](principles.md) rule 3.
5. Check it against [accessibility.md](accessibility.md)'s checklist.

## How to add a new component

1. Check [components.md](components.md) first — it may already cover the shape you need with a
   variant you haven't used yet (a `.btn-ghost` instead of a new button style, say).
2. If it's genuinely new, design it using only tokens from [design-tokens.md](design-tokens.md) —
   no new hex values, no new pixel values outside the spacing/radius scales.
3. Add it to `components.css` with the other components in its category, and add a short entry to
   [components.md](components.md) describing when to use it and when not to.

## Review checklist, before calling any UI work done

- [ ] Built only from existing tokens/components, or a deliberate, documented addition to them
- [ ] No blue or purple introduced as a non-semantic color (info-blue is fine; brand-blue is not)
- [ ] Populated / loading / empty / error states all designed
- [ ] Keyboard-only pass: every control reachable, every focus state visible
- [ ] Both themes checked, not just the one the browser happened to default to
- [ ] Responsive at 1440 / 1024 / 768 / ~380px, verified in a real narrow viewport if the tool's own
      emulation is in doubt (see [patterns.md](patterns.md#responsive-rules))
- [ ] No control that looks interactive but silently does nothing
- [ ] No fabricated data — every number/label traces to a real value or is honestly omitted
- [ ] `make check` (or the surface's equivalent) still passes — a UI change that breaks a backend
      test usually means a contract was touched, not just pixels
