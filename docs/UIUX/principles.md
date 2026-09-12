# Principles

## What "good" means here

The bar is: **a senior product designer at a company that takes craft seriously — Google, Stripe,
Linear — would look at this and see nothing to fix.** Not "impressive." Not "trendy." *Considered.*
Every spacing value, every color, every transition has a reason someone could state in one sentence.
If you can't state the reason, it's decoration, and decoration is the first thing that ages badly.

This system runs regulated banking automation. The UI's job is to make a person trust it faster and
work in it more accurately — not to look exciting. Calm reads as competent. Loud reads as unproven.

## The four rules

### 1. Restraint is the default, not the fallback

Start from nothing and add only what a specific screen needs. A page with three colors, one accent,
and clean type beats a page with six colors "for visual interest" every time. If a screen feels
empty, the fix is better spacing and hierarchy — not more color, more icons, or more borders.

**In practice:** one accent color for the whole product (see [design-tokens.md](design-tokens.md)).
Status/semantic colors exist and are used *only* for status. Never introduce a new hue because a
section "needs to pop."

### 2. Hierarchy comes from type, weight, and space — color is the last resort

A reader should be able to tell what matters on a screen with the color turned off (literally: check
it in grayscale). Size, weight, and spacing establish importance. Color confirms it, or flags a
state (success/warning/danger) — it does not carry hierarchy on its own.

**In practice:** a page title is bigger and bolder than a card title, which is bigger and bolder
than a label. A primary action is a filled button; a secondary action is an outlined or ghost one —
never two filled buttons of different colors competing for attention.

### 3. Every state is a real design, not an accident

Loading, empty, error, and populated are four different screens for the same view. Design all four.
"Empty" is not a blank white rectangle — it explains what's missing and, where there's an action
that would fix it, offers that action. "Error" says what happened in plain language, not a stack
trace. "Loading" uses a skeleton shaped like the content that's coming, not a generic spinner
dropped in the middle of an empty page.

**In practice:** before calling a screen done, ask "what does this look like with zero rows? With
one thousand rows? While the request is in flight? If the request fails?" — and look at all four,
not just the happy path with sample data.

### 4. The interface never claims something the system can't back up

If a control looks clickable, it does something real when clicked. If a setting looks toggleable,
toggling it has an effect (even a small, honestly-labeled one) — a form control that silently does
nothing is worse than no control at all, because it teaches the user to distrust every other control
on the page. If data isn't available, show "—" or an honest empty state; never fabricate a plausible
number to fill a gap. This rule outranks every visual preference in this document.

## Voice, for UI copy

Short, direct, lower-case except sentence starts and proper nouns. Say what happened, not what the
system is thinking ("Session claimed" not "Successfully claiming your session..."). Prefer a
specific noun over a vague one ("the mock back-office" not "the application"). Error messages name
what was expected and what was observed, the same discipline the run-result taxonomy already uses
for automation failures (`docs/design/error-taxonomy.md`) — a human reading the UI deserves the same
debuggability an agent reading `RunResult` gets.
