# UI/UX guidelines

This folder is the design system for every web surface in this repository — the operator console
(`src/cua/hitl/console/static/`), the marketing/docs site (`site/`), and anything built after this
is written. One visual language, one set of rules, so a page built in six months looks like it
belongs next to a page built today.

Read in this order:

| Document | Answers |
|---|---|
| [principles.md](principles.md) | What "good" means here, and the four rules that follow from it |
| [design-tokens.md](design-tokens.md) | The exact colors, type scale, spacing, radius, motion — copy-pasteable CSS |
| [components.md](components.md) | How to build a button, card, table, form, status indicator, dialog — once, correctly |
| [patterns.md](patterns.md) | Page shells, navigation, responsive rules, empty/loading/error states |
| [accessibility.md](accessibility.md) | The non-negotiable minimum, checked before anything ships |
| [workflow.md](workflow.md) | The actual process: where files live, how to add a page, the review checklist |

## The one-paragraph version

Neutral surfaces, one confident accent color (not blue, not purple), a type scale with real
hierarchy, generous and *consistent* spacing, borders instead of shadows to show structure, motion
that clarifies rather than decorates. Every interactive element has a visible focus state. Every
piece of state (loading, empty, error) is designed, not left as a blank div. Nothing here is
decoration for its own sake — if a rule doesn't make the interface clearer, faster to scan, or more
trustworthy, it doesn't belong.

## Scope

This governs raw HTML/CSS/JS surfaces built directly in this repo — the console and the site are
the two that exist today. It does not govern CLI output formatting (`cua`'s `typer` commands) or
evidence file formats; those have their own conventions documented elsewhere (see
[`docs/README.md`](../README.md)).

## Current state vs. target

The console (`src/cua/hitl/console/static/styles/tokens.css`) now uses the emerald accent
(`#0f7a5c` light / `#3fd6a0` dark) defined in [design-tokens.md](design-tokens.md), matching this
guideline. The site (`site/index.html`) is closer already: its base is neutral ink/graphite with
mint/emerald used for one semantic status.
