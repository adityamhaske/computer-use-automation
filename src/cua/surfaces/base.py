"""The `SurfaceDriver` port -- the seam between perceiving a surface and the recorded flow.

The brief asks us to name this seam explicitly, so: everything *below* this interface knows what an
accessibility tree, a CDP session or a DOM is. Everything *above* it knows only `UiSnapshot`,
`TargetDescriptor` and `Action`. A capability artifact lives entirely above the line, which is the
whole argument for why one could replay against a desktop driver without being rewritten.

A driver answers two questions and nothing more:

    observe()   what does the surface look like right now?
    dispatch()  please do this

Note that `dispatch` accepts only an `AuthorizedAction`. That is not decoration: it is how the
policy chokepoint is made structural rather than conventional. See ADR 0004.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from cua.domain.snapshot import Rect, UiSnapshot
from cua.policy.authorized import AuthorizedAction


class SessionInfo(BaseModel):
    """What this driver is currently attached to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    driver: str
    url: str = ""
    capabilities: tuple[str, ...] = ()
    """What this driver can do, in the vocabulary a capability's `surface.driver_capabilities`
    uses -- so a mismatch is caught before a run starts rather than mid-flow."""


class ActionResult(BaseModel):
    """What happened when an action was dispatched.

    Deliberately thin. A driver reports mechanical success -- did the click land, did the page
    navigate -- and never interprets what it means. Deciding whether the resulting screen is a
    business outcome, a recoverable condition or an unrecognized state is the executor's job,
    working from the declared vocabulary in the artifact. A driver that started classifying would
    be making policy decisions from the bottom of the stack.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    navigated: bool = False
    http_status: int | None = None
    message: str = ""
    duration_ms: int = 0


@runtime_checkable
class SurfaceDriver(Protocol):
    """What any surface must provide. Implemented today by Playwright/CDP; sketched for UIA."""

    def observe(self) -> UiSnapshot:
        """Capture the current state as a normalized snapshot.

        Must be stable: two observations of an unchanged surface must produce identical node ids,
        or determinism checks, drift detection and handoff reconciliation all become impossible.
        """
        ...

    def dispatch(self, action: AuthorizedAction) -> ActionResult:
        """Perform an authorized action. Accepts nothing else."""
        ...

    def screenshot(self, *, redact: tuple[Rect, ...] = ()) -> bytes:
        """Capture a PNG, with `redact` regions obscured.

        Redaction is a driver responsibility because only the driver knows pixel geometry. The
        decision about *what* is sensitive is made above, from the capability's declarations.
        """
        ...

    def bounds_for(self, node_ids: tuple[str, ...]) -> dict[str, Rect]:
        """Geometry for specific nodes.

        Separate from `observe()` on purpose: bounds require a round trip per node, `observe()` runs
        on every step of every discovery run, and almost nothing needs geometry. It is fetched when
        something actually does -- screenshot redaction, or a vision fallback.
        """
        ...

    def session_info(self) -> SessionInfo: ...

    def close(self) -> None: ...
