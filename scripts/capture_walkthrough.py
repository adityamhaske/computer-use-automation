"""Capture the screenshots used by the walkthrough page at `site/walkthrough/`.

Scripted rather than taken by hand so the walkthrough can be regenerated from a working tree
whenever the surfaces change, instead of slowly going stale against screenshots nobody can
reproduce. Every shot is of the real running application or the real operator console -- nothing
here is a mockup.

Light mode only, by instruction: the console is forced to its light theme before capture so the
whole walkthrough reads as one document.

    make app                                   # in another shell, on MOCK_APP_PORT
    python scripts/capture_walkthrough.py app

    cua console --sign-in --capability ... --arm-fault undeclared_dialog --input member_id=12345
    python scripts/capture_walkthrough.py console
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "site/walkthrough/shots"
APP = "http://127.0.0.1:8811"
CONSOLE = "http://127.0.0.1:8812"

# Wide enough that the frameset's navigation and content columns both read, short enough that a
# shot drops into the page without dominating it.
VIEWPORT = {"width": 1180, "height": 720}


def _save(page: Page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"{name}.png"
    page.screenshot(path=str(path))
    print(f"  {path.relative_to(ROOT)}")


def _sign_in(page: Page) -> None:
    sys.path.insert(0, str(ROOT))
    from apps.mock_bank.server import VALID_PW, VALID_USER

    page.goto(f"{APP}/login")
    page.fill('input[name="user"]', VALID_USER)
    page.fill('input[name="pw"]', VALID_PW)
    page.click('input[type="submit"]')
    page.wait_for_load_state()


def capture_app(page: Page) -> None:
    """The target application: what the automation has to drive, and what it has to read."""
    page.goto(f"{APP}/logout")
    page.goto(f"{APP}/")
    page.wait_for_load_state()
    _save(page, "01-signon")

    _sign_in(page)
    page.goto(f"{APP}/")
    page.wait_for_load_state()
    _save(page, "02-search")

    for name, member in [
        ("03-member-detail", "12345"),
        ("04-member-not-found", "99999"),
        ("05-closed-account", "24680"),
    ]:
        page.frame(name="content").goto(f"{APP}/member/{member}")
        page.wait_for_load_state()
        _save(page, name)

    # Arm the fault the escalation stage depends on, so the walkthrough can show the screen that
    # actually stops a run rather than describing it.
    page.request.post(f"{APP}/_control/arm", data={"fault": "undeclared_dialog"})
    page.frame(name="content").goto(f"{APP}/search")
    page.wait_for_load_state()
    _save(page, "06-undeclared-dialog")
    page.request.post(f"{APP}/_control/reset")


def capture_console(page: Page) -> None:
    """The operator console: the escalation queue, the handoff, and the session coming back."""
    page.goto(f"{CONSOLE}/")
    page.wait_for_load_state()
    # Light mode only, per the walkthrough's own rule.
    page.evaluate("localStorage.setItem('cua-theme','light')")
    page.reload()
    page.wait_for_timeout(1500)
    _save(page, "07-console-overview")

    page.goto(f"{CONSOLE}/#/interventions")
    page.wait_for_timeout(2000)
    _save(page, "08-intervention-queue")

    page.click("[data-claim]")
    page.wait_for_timeout(3500)
    _save(page, "09-operator-in-control")

    tail = [("10-runs-evidence", "#/runs"), ("11-capability-catalog", "#/capabilities")]
    for name, route in tail:
        page.goto(f"{CONSOLE}/{route}")
        page.wait_for_timeout(2000)
        _save(page, name)


def main() -> int:
    what = sys.argv[1] if len(sys.argv) > 1 else "app"
    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT, color_scheme="light")
        try:
            if what == "app":
                capture_app(page)
            elif what == "console":
                capture_console(page)
            else:
                print(f"unknown target {what!r}; expected 'app' or 'console'", file=sys.stderr)
                return 2
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
