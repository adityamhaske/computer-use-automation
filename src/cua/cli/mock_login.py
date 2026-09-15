"""Signing a standalone CLI invocation into the mock back-office before a capability runs.

Every capability recorded against `apps.mock_bank` assumes an already-authenticated session -- see
the entrypoint comment on `corebank.member.savings_balance@1.0.0.yaml`: "The frameset shell... This
app is only ever used through its frameset." A freshly booted `make app` has no session, so a bare
`cua replay` / `cua catalog invoke` / `cua agent-demo` lands on the sign-on screen and the
capability's own precondition correctly fails closed (AGENTS.md invariant 5: unknown states fail
closed) rather than guessing that the click probably worked.

This is demo/fixture setup, not a capability action: it drives the browser directly, the same way
`cua.cli.demo.Runner.sign_in` and `cua.evals.harness.sign_in` already do, and for the same reason --
placing a fixture in its starting state is not an automation decision a capability declares, so it
does not go through the policy chokepoint (AGENTS.md invariant 2). Escalating a login step through
PolicyEngine would mean every capability recorded against this app also has to declare a step for
logging in, which has nothing to do with what the capability actually does.

Opt-in only (the CLI's `--sign-in` flag) and scoped to the mock app's own fixed demo credentials --
this is not a general "log in to any target" mechanism, and it is never pointed at a real target.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cua.runtime.wiring import Rig


def _demo_credentials() -> tuple[str, str]:
    """The mock app's own fixed credentials, read from the app rather than duplicated.

    `apps/` is deliberately not part of the installed package -- it is a test fixture, not product
    code -- so it is importable only because the repository root is the working directory. That
    holds for `python -m cua.cli.main`, whose sys.path[0] is the cwd, and *not* for the `cua`
    console script, whose sys.path[0] is the venv's bin directory. The README documents the console
    script, so `cua replay --sign-in` raised ModuleNotFoundError while the identical `python -m`
    invocation worked.

    Adding the repository root explicitly is the narrow fix. It is guarded: if `apps` is genuinely
    absent this is an installed copy with no mock app, and `--sign-in` has nothing to sign in to --
    so say that, rather than failing with an import error four frames deep.
    """
    try:
        from apps.mock_bank.server import VALID_PW, VALID_USER
    except ModuleNotFoundError:
        root = Path(__file__).resolve().parents[3]
        if not (root / "apps").is_dir():
            raise RuntimeError(
                "--sign-in drives the bundled mock back-office, which is not present in this "
                "install. Run from a checkout of the repository."
            ) from None
        sys.path.insert(0, str(root))
        from apps.mock_bank.server import VALID_PW, VALID_USER

    return VALID_USER, VALID_PW


def sign_in(rig: Rig, base_url: str) -> None:
    """Put the mock app's session where an already-authenticated operator's would already be."""
    user, password = _demo_credentials()

    page = rig.driver.page
    page.goto(f"{base_url}/login")
    page.fill('input[name="user"]', user)
    page.fill('input[name="pw"]', password)
    page.click('input[type="submit"]')
    page.wait_for_load_state()
    page.goto(base_url)
    page.wait_for_load_state()
