"""The surface-neutral hand-off between a driver and the normalizer.

A driver extracts elements however its surface allows -- an accessibility tree over CDP, a UIA
element walk, an OCR pass -- and hands them over as `RawElement`s. Everything after that is shared.

This type lives in `perception` rather than in a driver on purpose. The import contract forbids
`perception` from importing `surfaces`, so putting it the other way round would mean either a
duplicated DTO or a broken boundary. Drivers depend on perception; perception never depends on a
driver.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RawElement:
    """One element as its surface reports it, before normalization.

    Roles and names are still in the surface's own vocabulary here -- `LayoutTableCell`, `Edit`,
    `AXButton`. Translating them is `normalize`'s job.
    """

    element_id: str
    """The surface's own handle. Session-scoped and not persisted."""

    role: str
    name: str | None = None
    value: str | None = None
    states: set[str] = field(default_factory=set)
    child_ids: list[str] = field(default_factory=list)
    parent_id: str | None = None
    frame: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    """Surface-specific extras that may become resolution hints. Never identity."""

    native_handle: int | None = None
    """The driver's own reference (a CDP backend node id, a UIA runtime id). Kept so the driver can
    act on the node it perceived, without leaking upward -- it never reaches the artifact."""
