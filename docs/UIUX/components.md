# Components

Concrete rules for the pieces every screen is built from. `src/cua/hitl/console/static/styles/
components.css` is the reference implementation of most of these class names and patterns — treat
that file's *structure* as the template when building a component library for a new surface; adapt
the *colors* to [design-tokens.md](design-tokens.md).

## Buttons

One filled ("primary") button per view at most. Everything else is secondary (outlined) or ghost
(text-only, for the lowest-emphasis actions — "Cancel", inline row actions).

- Primary: `background: var(--accent)`, white text. Reserve for the one action the screen wants the
  user to take.
- Secondary: `background: var(--surface)`, `border: 1px solid var(--border-strong)`. The default
  for anything that isn't the single primary action.
- Ghost: no border, no background, `color: var(--text-secondary)`; darkens to `var(--text)` on
  hover with a subtle `var(--surface-2)` background.
- Destructive: same shape as primary, `background: var(--danger)`. Always paired with a confirmation
  step for anything irreversible (see [patterns.md](patterns.md#confirmation)).
- Every button has a visible `:hover`, `:active` (a 1px downward nudge reads as a physical press),
  `:disabled` (50% opacity, no pointer), and `:focus-visible` state. Never remove the browser's
  default focus outline without replacing it with an equally visible one.

## Status indicators

A closed set of semantic tones — success, info, warning, danger, and neutral — each meaning exactly
one thing everywhere it appears. Never invent a new tone for a one-off; map the new status onto the
closest existing tone and let its *label* carry the specific meaning (a run that's `business_outcome`
and one that's literally `success` can both read as the "success" tone with different label text —
they're both "the run did what it was supposed to").

A status pill is a colored dot + label on a soft background of the same hue — never a solid,
saturated fill behind white text for routine status (that weight is reserved for a genuinely
blocking/dangerous state the user must not miss).

## Cards

The primary content container: `background: var(--surface)`, `border: 1px solid var(--border)`,
`border-radius: var(--radius-lg)`. A card optionally has a header (title + one right-aligned action)
separated from the body by a `1px solid var(--border)` rule — never a shadow to separate header from
body, a border reads as more precise.

Don't nest cards more than one level deep. If a "card inside a card" feels necessary, the outer
container should be a plain `<section>` with a heading, not another card — two stacked card borders
around the same content is visual noise, not hierarchy.

## Tables

A table's job is fast scanning, which means: a `<thead>` with small, uppercase, letter-spaced,
`--text-tertiary` column labels; `<tbody>` rows with a bottom border, never full grid lines (vertical
rules between every cell make a table feel like a spreadsheet, not a considered list); a hover state
on clickable rows; monospace for anything that's an id/hash/code, proportional for everything else.

Below ~720px, a table becomes a stack of cards, one per row, each cell labeled (`<td data-label=
"Column">`) — never force a data table to scroll horizontally on a phone as the *only* accommodation;
horizontal scroll is an acceptable *fallback* for something like a wide JSON viewer, not the primary
plan for tabular data.

## Forms

Label above the field, always visible (a placeholder is not a label — it disappears the moment
someone starts typing, which is exactly when they most need to confirm they're in the right field).
One field per logical row unless two fields are genuinely a pair (city/state). Validation errors
appear next to the field they belong to, in `--danger`, stated as what's expected — "Member number
must be 4–10 digits", not "Invalid input".

A read-only settings display (values sourced from config, not editable here) is never styled to look
like an input — see [patterns.md](patterns.md#settings) for the exact treatment. An element that
looks editable and isn't is a trust violation (principles.md rule 4).

## Empty / loading / error states

- **Empty:** an icon (outline style, `--text-tertiary`), a one-line title in `--text`, an optional
  one-sentence explanation in `--text-tertiary`, and — only if there's a real next action — one
  button. Centered, generous padding (`--space-9` vertical minimum).
- **Loading:** a skeleton shaped like the real content (a gray rounded block where a card will be, a
  few gray lines where table rows will be) with a subtle shimmer animation. A bare spinner is
  acceptable only for a sub-second action inside a button (e.g. "Saving...").
- **Error:** the same shape as empty, with a warning-toned icon, the actual error message (or a
  clear paraphrase of it — never swallow the detail entirely), and a retry action when retrying is
  meaningful.

## Dialogs

Use the native `<dialog>` element with its `::backdrop`. A dialog interrupts, so it's for
confirmations and short, focused forms only — never a full page's worth of content. Title, one or
two sentences of context, then right-aligned actions with the *less* consequential one first
(Cancel, then Confirm/Delete) so a reflexive click lands on the safe option.

## Toasts

Bottom-right, stacked, auto-dismiss after ~4 seconds, colored only by a left border accent (not a
filled background) so a string of them doesn't turn the corner of the screen into a wall of color.
One toast per user action — never batch multiple unrelated confirmations into one.

## Icons

Inline SVG, stroke-based, one consistent stroke weight across the whole icon set (1.5–1.75px at
24px viewBox). No icon font, no third-party icon package pulled in for a handful of icons — hand-
author or vendor the dozen or so you actually need (`src/cua/hitl/console/static/icons.js` is the
reference: plain exported SVG strings, no build step, no CDN dependency, renders identically
offline).

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
