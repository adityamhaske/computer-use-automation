"""`AuthorizedAction` -- the only thing a surface driver will accept.

The chokepoint made structural. `SurfaceDriver.dispatch()` takes an `AuthorizedAction`, and the only
way to obtain one is `PolicyEngine.authorize()`, because construction requires a token the engine
holds privately. Code that wants to skip policy has to *visibly* forge something rather than simply
call a driver method.

Three layers of enforcement, because an invariant that relies on everyone agreeing is weaker than
one nobody can violate by accident:

1. **Import rule** -- only `cua.runtime.dispatcher` may import `cua.surfaces` (`.importlinter`).
2. **This type** -- a hand-constructed action is rejected at runtime.
3. **Reconciliation** -- every dispatch in a run record must have a matching authorization, asserted
   by `tests/invariants/test_policy_chokepoint.py`.

Stated plainly, because it matters for how much weight to put on layer 2: Python has no real private
constructor, so the token stops *accidents*, not a determined caller. Layers 1 and 3 are the ones
that hold against intent. This is a limit worth knowing rather than a guarantee worth overselling.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from cua.domain.action import Action, ActionRisk
from cua.domain.actor import Actor


class _MintToken:
    """Sentinel proving an action came from PolicyEngine. Module-private by convention."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<policy mint token>"


MINT_TOKEN: Final = _MintToken()
"""Held by `PolicyEngine`. Passing it from anywhere else is the forgery this design makes
visible."""


class UnauthorizedActionError(RuntimeError):
    """Raised when something tries to build an AuthorizedAction without going through policy."""


@dataclass(frozen=True, slots=True)
class AuthorizedAction:
    """An action policy has approved, tagged with who is acting and under which grant of control."""

    action: Action
    actor: Actor
    session_id: str
    lease_epoch: int
    """The lease generation this authorization belongs to.

    A dispatch carrying a stale epoch is refused with LEASE_LOST. That kills a real race rather than
    a theoretical one: escalation happens *while* a step is in flight, and without this check that
    step can land on the page a half-second after a human has taken over and started typing.
    """

    decision_id: str
    """Ties this to its authorization event in the evidence stream, so the reconciliation test can
    match dispatches to decisions."""

    risk: ActionRisk
    """As classified by policy -- not as annotated in the artifact. The artifact's annotation is one
    of three inputs to the classification, never the conclusion."""

    resolved_node_id: str | None = None
    """Filled in by the resolver, after authorization and before dispatch."""

    requires_confirmation: bool = False
    """An irreversible action a human operator may perform, but only after confirming explicitly."""

    _token: _MintToken | None = None

    def __post_init__(self) -> None:
        if self._token is not MINT_TOKEN:
            raise UnauthorizedActionError(
                f"{type(self).__name__} must be minted by PolicyEngine.authorize(). "
                "Nothing reaches a surface without passing policy -- see AGENTS.md invariant 2."
            )

    def with_resolution(self, node_id: str) -> AuthorizedAction:
        """Attach the resolved target. Preserves the authorization; does not re-open it.

        Resolution happens *after* authorization (Action -> Policy -> Resolver -> Driver), so the
        approved action has to be able to carry its node without being rebuilt -- rebuilding would
        mean re-minting, and re-minting outside the engine is exactly what this type prevents.
        """
        return replace(self, resolved_node_id=node_id)

    def __repr__(self) -> str:
        return (
            f"AuthorizedAction({self.action.type}, actor={self.actor.value}, "
            f"risk={self.risk.value}, epoch={self.lease_epoch})"
        )
