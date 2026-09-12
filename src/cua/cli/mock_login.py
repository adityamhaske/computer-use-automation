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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cua.runtime.wiring import Rig


def sign_in(rig: Rig, base_url: str) -> None:
    """Put the mock app's session where an already-authenticated operator's would already be."""
    from apps.mock_bank.server import VALID_PW, VALID_USER

    page = rig.driver.page
    page.goto(f"{base_url}/login")
    page.fill('input[name="user"]', VALID_USER)
    page.fill('input[name="pw"]', VALID_PW)
    page.click('input[type="submit"]')
    page.wait_for_load_state()
    page.goto(base_url)
    page.wait_for_load_state()
