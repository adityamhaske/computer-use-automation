"""AGENTS.md invariant 2: no action reaches a driver without policy authorization.

Enforced three independent ways, because an invariant everyone agrees with is weaker than one
nobody can violate by accident:

    1. an import rule      -- only cua.runtime may import cua.surfaces
    2. a type rule         -- dispatch() accepts only a policy-minted AuthorizedAction
    3. a reconciliation    -- every dispatch in a run record has a matching authorization

Layer 2's limit is stated plainly in `cua/policy/authorized.py`: Python has no real private
constructor, so the token stops accidents, not a determined caller. Layers 1 and 3 are what hold
against intent. These tests cover all three.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cua.domain.action import ActionRisk, Click
from cua.domain.actor import Actor
from cua.domain.target import TargetDescriptor
from cua.policy.authorized import MINT_TOKEN, AuthorizedAction, UnauthorizedActionError

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src/cua"

# Root-level Python that is not part of the `cua` package, and so is invisible to `.importlinter`
# (rooted at `cua`) and to mypy (`packages = ["cua"]`). The eval harness was almost written here for
# exactly that reason -- it drives a real browser, and at the root it would have escaped all three
# enforcement layers. It lives under `src/cua/evals/` instead. This list is what catches the next
# module that tries.
OUTSIDE = ("scripts", "apps")


# ------------------------------------------------------- 1. the import rule


def _calls_dispatch(path: Path) -> bool:
    """True if this module actually *calls* `.dispatch(...)`.

    Parsed rather than grepped: the substring appears in several docstrings that explain the
    chokepoint, and a test that cannot tell an explanation from a call would be permanently red
    for the wrong reason.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dispatch"
        for node in ast.walk(tree)
    )


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_only_runtime_imports_surfaces() -> None:
    """`.importlinter` enforces this too. Asserting it here as well means the claim survives
    someone deleting a contract from that file -- which would otherwise turn the suite green."""
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC)
        if relative.parts[0] in ("runtime", "surfaces"):
            continue
        if any(name.startswith("cua.surfaces") for name in _imports_of(path)):
            offenders.append(str(relative))

    # Root-level packages too: they are outside `.importlinter`'s root package, so this scan is
    # the only thing standing between them and a driver.
    for directory in OUTSIDE:
        base = ROOT / directory
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if any(name.startswith("cua.surfaces") for name in _imports_of(path)):
                offenders.append(str(path.relative_to(ROOT)))

    assert not offenders, (
        f"{offenders} import cua.surfaces directly, bypassing the policy chokepoint. "
        "Only cua.runtime.dispatcher may touch a driver -- see AGENTS.md invariant 2."
    )


def test_only_the_dispatcher_dispatches() -> None:
    """Within `runtime`, more than one module may *read* from a driver -- failure capture needs a
    screenshot and node geometry. Only one may *act*.

    So the rule that matters is narrower than "one module imports surfaces": exactly one module
    calls `.dispatch(`, and that is where authorization is checked.
    """
    dispatchers = sorted(
        str(path.relative_to(SRC)) for path in SRC.rglob("*.py") if _calls_dispatch(path)
    )
    assert dispatchers == ["runtime/dispatcher.py"], (
        f"{dispatchers} call .dispatch() on a driver. Authorization is checked in "
        "runtime/dispatcher.py, so a second call site is a second, unchecked path to the surface."
    )


# --------------------------------------------------------- 2. the type rule


def _unauthorized_kwargs() -> dict[str, object]:
    return {
        "action": Click(target=TargetDescriptor(role="button")),
        "actor": Actor.AUTOMATION,
        "session_id": "s1",
        "lease_epoch": 1,
        "decision_id": "forged",
        "risk": ActionRisk.SAFE,
    }


def test_a_hand_built_authorization_is_rejected() -> None:
    """The forged path: what code trying to skip policy would actually write."""
    with pytest.raises(UnauthorizedActionError):
        AuthorizedAction(**_unauthorized_kwargs())  # type: ignore[arg-type]


def test_a_wrong_token_is_rejected() -> None:
    with pytest.raises(UnauthorizedActionError):
        AuthorizedAction(**_unauthorized_kwargs(), _token=object())  # type: ignore[arg-type]


def test_resolution_preserves_authorization_without_re_minting() -> None:
    """Resolution happens after authorization, so an approved action must be able to carry its
    node without being rebuilt -- rebuilding would mean re-minting, outside the engine."""
    authorized = AuthorizedAction(**_unauthorized_kwargs(), _token=MINT_TOKEN)  # type: ignore[arg-type]
    resolved = authorized.with_resolution("content:/table[0]/row[1]/textbox[0]")

    assert resolved.resolved_node_id == "content:/table[0]/row[1]/textbox[0]"
    assert resolved.decision_id == authorized.decision_id
    assert resolved.actor is authorized.actor


# ---------------------------------------------------- 3. the reconciliation


def test_driver_dispatch_accepts_only_an_authorized_action() -> None:
    """The signature is the enforcement. A driver that took a plain Action would make the other
    two layers cosmetic."""
    import inspect

    from cua.surfaces.playwright_cdp.driver import PlaywrightCdpDriver

    signature = inspect.signature(PlaywrightCdpDriver.dispatch)
    annotation = signature.parameters["action"].annotation
    assert annotation in (AuthorizedAction, "AuthorizedAction")
