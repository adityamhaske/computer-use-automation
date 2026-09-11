"""Reproducible spike: is the semantic tree usable on a frameset + table-layout legacy app?

This is the measurement behind ADR 0001 and the Phase 03 design. It is kept runnable rather than
written up only, because "we measured this" is a better answer than "we assumed this" -- and because
a reviewer should be able to check the claim rather than take it on faith.

    make app                                    # terminal 1 (base, :8811)
    make app-variant-b MOCK_APP_PORT=8821       # terminal 2 (optional, for the variant comparison)
    .venv/bin/python scripts/spike_semantic_tree.py

Findings are recorded in docs/prd/phases/phase-03-perception-driver.md.
"""

from __future__ import annotations

import sys
from typing import Any

from playwright.sync_api import Browser, Page, sync_playwright

BASE_URL = "http://127.0.0.1:8811"
VARIANT_B_URL = "http://127.0.0.1:8821"
INTERACTIVE = {"textbox", "button", "link", "combobox", "listbox", "checkbox"}


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/login")
    page.fill('input[name="user"]', "teller01")
    page.fill('input[name="pw"]', "not-a-real-password")
    page.click('input[type="submit"]')
    page.wait_for_load_state()


def frame_ids(cdp: Any) -> dict[str, str]:
    """Frame name -> frame id. Needed because the AX tree does not cross frame boundaries."""
    ids: dict[str, str] = {}

    def walk(node: dict[str, Any]) -> None:
        frame = node["frame"]
        ids[frame.get("name") or "<main>"] = frame["id"]
        for child in node.get("childFrames", []):
            walk(child)

    walk(cdp.send("Page.getFrameTree")["frameTree"])
    return ids


def ax_nodes(cdp: Any, frame_id: str | None = None) -> list[dict[str, Any]]:
    params = {"frameId": frame_id} if frame_id else {}
    result = cdp.send("Accessibility.getFullAXTree", params)
    return [n for n in result.get("nodes", []) if not n.get("ignored")]


def named(node: dict[str, Any]) -> tuple[str | None, str | None]:
    return (node.get("role") or {}).get("value"), (node.get("name") or {}).get("value")


def report(browser: Browser, label: str, base: str) -> bool:
    context = browser.new_context()
    page = context.new_page()
    try:
        sign_in(page, base)
    except Exception as exc:
        print(f"{label}: not reachable at {base} ({type(exc).__name__}) -- skipped\n")
        return False

    page.goto(base)
    page.wait_for_load_state()

    cdp = context.new_cdp_session(page)
    cdp.send("DOM.enable")
    cdp.send("Accessibility.enable")

    print("=" * 76)
    print(f"{label}  ({base})")
    print("=" * 76)

    # FINDING 1: the page-level tree stops at frame boundaries.
    page_level = ax_nodes(cdp)
    print(f"\n1. Page-level getFullAXTree ..... {len(page_level)} nodes (stops at frames)")
    for node in page_level:
        role, name = named(node)
        print(f"      {role!s:14} name={name!r}")

    # FINDING 2: per-frame retrieval gives the real tree.
    ids = frame_ids(cdp)
    content_id = ids.get("content")
    if content_id is None:
        print("\n   !! no 'content' frame found")
        context.close()
        return False

    frame_level = ax_nodes(cdp, content_id)
    controls = [named(n) for n in frame_level if named(n)[0] in INTERACTIVE]
    print(f"\n2. Per-frame getFullAXTree ...... {len(frame_level)} nodes in 'content'")
    print("   interactive controls:")
    for role, name in controls:
        print(f"      {role!s:14} name={name!r}")

    # FINDING 3: row structure is explicit, so structural anchoring is implementable.
    print("\n3. Structural view (row -> label cell -> value cell):")
    snapshot = page.frame(name="content").locator("table").nth(2).aria_snapshot()
    for line in snapshot.splitlines():
        if any(k in line for k in ("row ", "cell", "textbox", "button")):
            print("      " + line.strip())

    context.close()
    print()
    return True


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        reachable = report(browser, "BASE TENANT", BASE_URL)
        report(browser, "VARIANT B (second tenant, same product)", VARIANT_B_URL)
        browser.close()

    if not reachable:
        print("Start the app first:  make app")
        return 1

    print("=" * 76)
    print("Conclusions (see docs/prd/phases/phase-03-perception-driver.md)")
    print("=" * 76)
    print("  - The AX tree does NOT cross frames; the driver must stitch per-frame trees.")
    print("  - Controls carry real accessible names, so semantic_exact is viable.")
    print("  - Row -> label -> value cell is explicit, so structural_anchor is viable.")
    print("  - A relabeled tenant changes the anchor text too, so rebranding needs a")
    print("    TenantBinding overlay rather than a cleverer resolver.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
