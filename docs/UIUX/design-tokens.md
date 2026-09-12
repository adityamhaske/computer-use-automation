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

  /* surfaces */
  --bg: #fafaf9;           /* page background — warm, not clinical white */
  --surface: #ffffff;       /* cards, panels */
  --surface-2: #f4f4f3;     /* sunken areas, hover states */
  --border: #e4e4e1;
  --border-strong: #d0d0cc;
  --overlay: rgba(20, 20, 18, 0.45);

  /* text */
  --text: #16160f;
  --text-secondary: #56564d;
  --text-tertiary: #8a8a7e;
  --text-on-accent: #ffffff;

  /* accent — emerald, not blue/purple */
  --accent: #0f7a5c;
  --accent-hover: #0c6249;
  --accent-active: #0a4f3b;
  --accent-soft: #e6f5ef;
  --accent-soft-strong: #cdebe0;
  --accent-text: #0c6249;
  --focus-ring: #0f7a5c;

  /* semantic status — used ONLY for status, never as decoration */
  --success: #157a4a;
  --success-soft: #e6f6ec;
  --info: #3f6b8a;          /* a desaturated slate-blue is fine here: status blue, not brand blue */
  --info-soft: #eaf1f6;
  --warning: #92660a;
  --warning-soft: #fdf1de;
  --danger: #a52c2c;
  --danger-soft: #fbeaea;
  --neutral-soft: #eeeeec;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #14140f;
    --surface: #1c1c16;
    --surface-2: #232319;
    --border: #302f26;
    --border-strong: #403e30;
    --overlay: rgba(8, 8, 6, 0.6);

    --text: #f2f2ec;
    --text-secondary: #b8b8aa;
    --text-tertiary: #7d7c6e;

    --accent: #3fd6a0;
    --accent-hover: #5ce0af;
    --accent-active: #6ee6ba;
    --accent-soft: rgba(63, 214, 160, 0.14);
    --accent-soft-strong: rgba(63, 214, 160, 0.22);
    --accent-text: #6ee6ba;
    --focus-ring: #3fd6a0;

    --success: #3fd693;
    --success-soft: rgba(63, 214, 147, 0.13);
    --info: #8fb4cc;
    --info-soft: rgba(143, 180, 204, 0.13);
    --warning: #f0b23f;
    --warning-soft: rgba(240, 178, 63, 0.13);
    --danger: #ff7a7a;
    --danger-soft: rgba(255, 122, 122, 0.13);
    --neutral-soft: #262620;
  }
}
/* :root[data-theme="dark"] repeats the block above; :root[data-theme="light"] just sets
   color-scheme: light. See tokens.css for the full four-block pattern and why each exists. */
```

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
