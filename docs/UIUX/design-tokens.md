# Design tokens

The exact values. Every web surface in this repo defines these as CSS custom properties on `:root`
(see `src/cua/hitl/console/static/styles/tokens.css` for the reference implementation pattern —
copy its *structure*, not its current color values; those predate this guideline, see
[README.md](README.md#current-state-vs-target)).

## Color

**One accent. Not blue, not purple.** The accent here is a restrained, slightly cool emerald —
confident without being loud, distinct from the mint/emerald `site/index.html` already uses as a
semantic "success" color (that's fine: one hue can be both the brand accent *and* the success
color, the way GitHub's green and Linear's purple both do double duty — it reads as intentional,
not as a collision, because there is only ever the one non-neutral hue on screen at a time).

```css
:root {
  color-scheme: light;

  /* surfaces — warm neutrals (hue ~35°), never the cool blue-grey dashboards default to */
  --bg: #faf9f7;
  --surface: #ffffff;
  --surface-2: #f4f2ef;
  --surface-raised: #ffffff;
  --border: #e8e4de;
  --border-strong: #d4cec6;
  --overlay: rgba(28, 26, 23, 0.38);

  /* text */
  --text: #1c1a17;
  --text-secondary: #59544c;
  --text-tertiary: #6e685f;
  --text-on-accent: #ffffff;

  /* accent — deep emerald. No blue, no purple, anywhere. */
  --accent: #146b52;
  --accent-hover: #10583f;
  --accent-active: #0d4633;
  --accent-soft: #e8f2ed;
  --accent-soft-strong: #d2e7dd;
  --accent-text: #10583f;
  --focus-ring: #146b52;

  /* semantic status — status only, never decoration */
  --success: #2f6b46;
  --success-soft: #e7f2ea;
  --running: #6b665d;        /* neutral graphite: a run simply proceeding asks nothing of anyone */
  --running-soft: #f0ede8;
  --human: #146b52;
  --human-soft: #e8f2ed;
  --warning: #8a6410;
  --warning-soft: #fbf1dd;
  --danger: #9c3428;
  --danger-soft: #f9eae7;
  --neutral-soft: #f0ede8;
}

/* Dark is OPT-IN, never automatic — there is no prefers-color-scheme block. This console is read
   in daylit back offices; it does not follow the OS into dark mode on its own. */
:root[data-theme="dark"] {
  color-scheme: dark;

  --bg: #12110f;
  --surface: #1a1816;
  --surface-2: #211f1c;
  --surface-raised: #24211e;
  --border: #302c28;
  --border-strong: #423d37;
  --overlay: rgba(8, 7, 6, 0.62);

  --text: #f0ede8;
  --text-secondary: #b5aea4;
  --text-tertiary: #928b81;
  --text-on-accent: #10231c;

  --accent: #5cbf99;
  --accent-hover: #74cfab;
  --accent-active: #8bd9bb;
  --accent-text: #74cfab;
  --focus-ring: #5cbf99;

  --success: #57bd8b;
  --running: #9d968c;
  --human: #5cbf99;
  --warning: #d9a441;
  --danger: #e08b80;
  --neutral-soft: #262320;
}
```

**Two rules this palette is built on.**

*Hue carries meaning, or it is not used.* Green is healthy and human-held, amber wants attention,
brick is failure — and `--running` is a neutral graphite on purpose, because a run that is simply
proceeding is not asking the operator for anything. Reaching for a colour to distinguish a state
nobody needs to act on is how a status palette stops meaning anything.

*Light is the default, unconditionally.* Every value above clears WCAG AA on every surface it is
used on, in both themes — verified, not assumed.


**Rule:** if a screen needs a second non-neutral color for something that isn't a status, that's a
sign the layout needs better hierarchy, not another hue. Take it to [principles.md](principles.md)
rule 1 before adding one.

## Typography

```css
--font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Helvetica, Arial, sans-serif;
--font-mono: ui-monospace, "SF Mono", "Cascadia Code", Menlo, Consolas, monospace;

--text-2xs: 11px;  --text-xs: 12px;  --text-sm: 13px;  --text-md: 14px;
--text-lg: 16px;   --text-xl: 20px;  --text-2xl: 26px; --text-3xl: 34px;

--weight-regular: 400;  --weight-medium: 500;
--weight-semibold: 600; --weight-bold: 700;

--leading-tight: 1.25;  --leading-normal: 1.5;
```

No custom web fonts by default — the system stack renders instantly, looks native on every
platform, and needs no external request (a page that phones a font CDN before it can render its
first word is not "on point"). If a page genuinely needs a distinct display face for a hero/marketing
moment, load it from `fonts.googleapis.com`/`fonts.gstatic.com` only, with `font-display: swap`, and
never for body text.

A heading is bigger *and* bolder than what it introduces — never one or the other alone (a same-size
bold-vs-regular pair reads as a formatting glitch, not a hierarchy).

## Spacing

An 8px rhythm (4px only for the tightest internal gaps — icon-to-label, badge padding).

```css
--space-1: 4px;  --space-2: 8px;  --space-3: 12px; --space-4: 16px; --space-5: 20px;
--space-6: 24px; --space-7: 32px; --space-8: 40px; --space-9: 48px; --space-10: 64px;
```

Never a bespoke pixel value outside this scale. If nothing in the scale looks right, the layout is
wrong, not the scale.

## Radius

```css
--radius-sm: 6px;   /* buttons, inputs, badges */
--radius-md: 10px;  /* cards */
--radius-lg: 14px;  /* panels, dialogs */
--radius-full: 999px; /* pills, avatars, the theme toggle */
```

## Shadow

Shadows are rare here — borders do most of the work of showing structure, which is why the palette
above defines `--border`/`--border-strong` so carefully. Reserve shadow for things that visually
float above the page: dialogs, dropdowns, toasts.

```css
--shadow-sm: 0 1px 2px rgba(20, 20, 18, 0.06);
--shadow-md: 0 4px 16px rgba(20, 20, 18, 0.10), 0 1px 3px rgba(20, 20, 18, 0.08);
--shadow-lg: 0 12px 36px rgba(20, 20, 18, 0.16), 0 2px 6px rgba(20, 20, 18, 0.08);
```

In dark mode, shadows barely register against a dark background — lean on `--border`/`--surface-2`
contrast instead; keep the shadow tokens for elevation logic but expect them to do less visual work.

## Motion

```css
--ease: cubic-bezier(0.2, 0, 0, 1);
--duration-fast: 120ms;   /* hover, focus, small state changes */
--duration-base: 180ms;   /* panel/page transitions */
--duration-slow: 260ms;   /* drawers, larger reveals */
```

Motion explains a state change (a drawer sliding in shows *where* it came from); it never exists
just to feel alive. Always respect `prefers-reduced-motion` — collapse everything to `0.001ms` under
that query rather than degrading gracefully, so it is genuinely off, not just shorter.

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
