"""Turning a surface's raw element tree into the normalized vocabulary.

Surfaces disagree about what to call things. A browser accessibility tree says `LayoutTableCell`
where Windows UIA says `DataItem` and macOS says `AXCell`. If that vocabulary leaked upward, every
artifact would silently encode which surface it was recorded against, and the portability claim in
ADR 0001 would be false.

So normalization happens here, at the bottom, and everything above speaks one language.

This module is **surface-neutral** -- enforced by the `surface-neutral-targeting` import contract.
It cannot import Playwright, and it never sees a DOM. It takes already-extracted raw values and maps
them; the extraction itself is the driver's job.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

# --------------------------------------------------------------------- roles

ROLE_ALIASES: dict[str, str] = {
    # --- browser accessibility tree (what our Chromium driver produces) ---
    "rootwebarea": "document",
    "iframe": "frame",
    "layouttable": "table",
    "layouttablerow": "row",
    "layouttablecell": "cell",
    "gridcell": "cell",
    "columnheader": "cell",
    "rowheader": "cell",
    "statictext": "text",
    "inlinetextbox": "text",
    "paragraph": "text",
    "genericcontainer": "group",
    "searchbox": "textbox",
    "img": "image",
    "graphicsdocument": "image",
    "listbox": "combobox",
    "menulistpopup": "combobox",
    "menulistoption": "option",
    # --- Windows UI Automation (the desktop driver this design is meant to allow) ---
    # Listed to make the portability claim concrete: these are the mappings a UIA driver would use,
    # and the point is that they land in the same vocabulary, so the same artifact resolves.
    "edit": "textbox",
    "hyperlink": "link",
    "dataitem": "cell",
    "header": "row",
    "custom": "group",
    "pane": "group",
    "tabitem": "tab",
}

NOISE_ROLES: frozenset[str] = frozenset({"linebreak", "inlinetextbox", "none", "presentation"})
"""Roles carrying no information a capability could target.

Dropped for a practical reason as much as a tidiness one: a snapshot is rendered into an LLM prompt
during discovery, and a frameset app produces hundreds of these. Keeping them costs tokens and
buries the controls that matter.
"""


def normalize_role(raw: str | None) -> str:
    if not raw:
        return "unknown"
    key = raw.strip().lower().replace(" ", "").replace("_", "")
    return ROLE_ALIASES.get(key, key)


def is_noise(raw_role: str | None) -> bool:
    if not raw_role:
        return False
    return raw_role.strip().lower().replace(" ", "") in NOISE_ROLES


# --------------------------------------------------------------------- names


def normalize_name(raw: str | None) -> str | None:
    """Clean an accessible name without changing what it says.

    Collapses whitespace and strips non-breaking spaces and trailing separators -- legacy templates
    are full of `&nbsp;` and `Member Number:` -- while leaving the words alone. Deliberately *not*
    fuzzy: "Member #" must not become "Member Number", or resolution would start guessing.
    """
    if raw is None:
        return None
    cleaned = raw.replace("\xa0", " ").replace("​", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip(":*").strip()
    return cleaned or None


def fold(text: str | None) -> str:
    """Aggressive fold for tolerant comparison: case, punctuation and spacing removed.

    Used by `semantic_normalized`, one rung below exact matching. Still not fuzzy: "member#" and
    "membernumber" remain different strings.
    """
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


# ------------------------------------------------------------------ identity


def make_node_id(frame: str | None, path: Sequence[tuple[str, int]]) -> str:
    """A stable identifier for a node's position in the tree.

    Derived from structure -- frame plus the role/index path from the root -- and never from a
    counter, an address, or the order nodes happened to be visited. Two observations of an unchanged
    page must produce identical ids, or nothing downstream can compare snapshots: no determinism
    check, no drift detection, and no way to tell what a human changed during a handoff.

    Example: ``content:/table[0]/row[2]/cell[1]/textbox[0]``
    """
    trail = "/".join(f"{role}[{index}]" for role, index in path)
    return f"{frame or 'main'}:/{trail}"


def fingerprint(
    role: str,
    name: str | None,
    ancestor_roles: Sequence[str],
    frame: str | None = None,
) -> str:
    """A short structural signature, compared between discovery and replay to detect drift.

    Includes the ancestor chain because that is what actually moves when a vendor reworks a screen:
    a button keeping its name but moving from a toolbar into a dialog is a meaningful change, and a
    signature that ignored structure would call it identical.
    """
    payload = "|".join([frame or "main", role, fold(name), ">".join(ancestor_roles)])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ------------------------------------------------------------------- regions

REGION_HINTS: tuple[tuple[str, str], ...] = (
    ("search", "search_form"),
    ("find", "search_form"),
    ("sign on", "login_form"),
    ("sign in", "login_form"),
    ("login", "login_form"),
    ("detail", "detail_view"),
    ("record", "detail_view"),
    ("account", "accounts_view"),
    ("confirm", "confirmation"),
)


def infer_region(heading_text: str | None) -> str | None:
    """Best-effort logical region from a nearby heading.

    Best-effort on purpose: a region is a *disambiguator*, not an identity. It lets a descriptor say
    "the Search button in the search form" when a page has two, and nothing depends on it being
    right -- if inference fails the descriptor simply has one less way to narrow down, and falls
    through to another rung rather than resolving incorrectly.
    """
    if not heading_text:
        return None
    lowered = heading_text.lower()
    for needle, region in REGION_HINTS:
        if needle in lowered:
            return region
    return None
