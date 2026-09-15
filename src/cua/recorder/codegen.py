"""Emit a runnable Playwright test from a capability artifact.

The artifact already describes a control by role, accessible name and the label beside it. That is
almost exactly Playwright's own locator vocabulary, so a test generated from it is a translation
rather than an invention -- which is the point worth making: because the artifact never mentions a
DOM or a selector, the same document that drives deterministic replay also describes a test any
engineer can read, edit and run without this system present.

**A test rather than a page object**, because CI can run a test. "Runnable" then stops being a
claim about generated code and becomes something the build fails on.

Two rules this generator holds to:

*No CSS.* `target.hints["css"]` exists in the artifact as a cache and is deliberately not read
here. Emitting it would be the shortest path to a passing test and would quietly reintroduce the
coupling the artifact design exists to remove -- a generated test that breaks on a restyle is
worse than no generated test, because it looks like coverage.

*Exact labels, not substrings.* `adjacent_to: "Status"` becomes "the row containing a cell whose
accessible name is exactly Status", not "a row containing the text Status". The mock app has a
`Status` column header as well as a `Status` summary row, and a substring filter matches both --
so the naive translation is ambiguous exactly where the resolver's anchor semantics are precise.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from cua.domain.action import Click, Extract, PressKey, Select, Type
from cua.domain.capability import Capability
from cua.domain.values import InputRef

# What a declared output type looks like once read off the screen. The generated test asserts
# shape, not value: the balance is fixture data and may change, but "money still looks like money"
# is the assertion that catches a capability reading the wrong cell.
SHAPE = {
    "money": r"^\$?[0-9,]+\.[0-9]{2}$",
    "date": r"^[0-9]{2}/[0-9]{2}/[0-9]{4}$",
    "integer": r"^-?[0-9]+$",
    "number": r"^-?[0-9]+(\.[0-9]+)?$",
}


class UnsupportedStepError(ValueError):
    """A step this generator cannot translate honestly.

    Raised rather than emitted as a comment or a skipped assertion. A generated file that silently
    omits a step still runs, still passes, and no longer tests what it claims to -- which is worse
    than refusing to generate one.
    """


def _frame(step_frame: str | None) -> str:
    """The Playwright handle for the frame a target is scoped to.

    Emitted as a call rather than a cached variable: a `Frame` handle goes stale the moment its
    page navigates, and this capability clicks Search in the middle of the flow. A generated test
    that cached the handle would fail on the step after the first click, for a reason that has
    nothing to do with the capability.
    """
    if not step_frame:
        return "page"
    return f'frame(page, "{step_frame}")'


def _locator(target: object) -> str:
    """One target, as a Playwright locator expression."""
    role = getattr(target, "role", None)
    name = getattr(target, "name", None)
    anchor = getattr(target, "anchor", None)
    scope = getattr(target, "scope", None)
    frame = _frame(getattr(scope, "frame", None) if scope else None)

    if name is not None and getattr(name, "value", None):
        return f'{frame}.get_by_role("{role}", name="{name.value}", exact=True)'

    if anchor is not None and getattr(anchor, "text", None):
        # `adjacent_to` means "the cell immediately after the one holding this label", and it is
        # emitted as exactly that: find the label cell, take the next sibling.
        #
        # The obvious translation -- filter rows by the label, then take the second cell -- is
        # wrong on a legacy table-layout page. This application nests its content inside an outer
        # layout table, so an outer row *also* contains the label, the filter matches it too, and
        # `.nth(1)` reads the whole page as a single value. A sibling step cannot climb.
        #
        # An XPath axis rather than a CSS selector: this is a structural relation, the same one
        # the resolver's anchor expresses, not a hook into how the page is styled.
        return (
            f'{frame}.get_by_role("cell", name="{anchor.text}", exact=True)'
            f'.locator("xpath=following-sibling::*[1]")'
        )

    raise UnsupportedStepError(
        f"target has neither an accessible name nor a label anchor: {target!r}"
    )


def _value(action: object, inputs: dict[str, str]) -> str:
    """The literal to type, resolved from the supplied inputs."""
    raw = getattr(action, "value", "")
    if isinstance(raw, InputRef):
        if raw.input_name not in inputs:
            raise UnsupportedStepError(
                f"step needs input {raw.input_name!r}; "
                f"pass it with --input {raw.input_name}=<value>"
            )
        return repr(inputs[raw.input_name])
    return repr(str(raw))


def render_test(capability: Capability, inputs: dict[str, str], *, base_url: str) -> str:
    """A standalone pytest module exercising this capability against a live application."""
    body: list[str] = []
    reads: list[tuple[str, str]] = []

    for step in capability.steps:
        action = step.action
        target = getattr(action, "target", None)
        body.append(f"# {step.id}: {step.description or action.type}")

        if isinstance(action, Type):
            body.append(f"{_locator(target)}.fill({_value(action, inputs)})")
        elif isinstance(action, Click):
            body.append(f"{_locator(target)}.click()")
            body.append("page.wait_for_load_state()")
        elif isinstance(action, Select):
            body.append(f"{_locator(target)}.select_option({_value(action, inputs)})")
        elif isinstance(action, PressKey):
            body.append(f'page.keyboard.press("{action.key}")')
        elif isinstance(action, Extract):
            name = action.into
            body.append(f"{name} = {_locator(target)}.inner_text().strip()")
            spec = next((o for o in capability.outputs if o.name == name), None)
            reads.append((name, spec.type if spec else "string"))
        else:
            raise UnsupportedStepError(f"{step.id}: no translation for {type(action).__name__}")
        body.append("")

    for name, kind in reads:
        pattern = SHAPE.get(kind)
        if pattern:
            # `repr` already produces a valid Python literal with its backslashes escaped. Prefixing
            # `r` on top of that made them literal pairs, so `\\$?` parsed as a quantifier
            # with nothing to repeat and every generated test died in `re.compile`.
            body.append(f"assert re.fullmatch({pattern!r}, {name}), (")
            body.append(f'    f"{name} did not look like {kind}: {{{name}!r}}"')
            body.append(")")
        else:
            body.append(f'assert {name}, "{name} was empty"')

    supplied = ", ".join(f"{k}={v!r}" for k, v in sorted(inputs.items()))
    return TEMPLATE.format(
        ref=capability.ref,
        title=capability.title,
        content_hash=capability.content_hash or "(unsealed)",
        generated=datetime.now(UTC).strftime("%Y-%m-%d"),
        base_url=base_url,
        supplied=supplied or "no inputs",
        test_name=re.sub(r"[^a-z0-9]+", "_", capability.id.lower()).strip("_"),
        # Indented here rather than per line: the body sits inside `with` and `try`.
        body="\n".join(("            " + line if line else "") for line in body).rstrip(),
    )


TEMPLATE = '''"""Generated from {ref} -- do not edit.

{title}

Generated on {generated} from an artifact sealed as:
    {content_hash}

Every locator below comes from that artifact's semantic target descriptors: role, accessible name,
and the label beside a value. No CSS selector is used, so this test survives a restyle for the same
reason the capability does.

Regenerate rather than edit:
    cua codegen {ref} --input ... --out <this file>

Run against a running mock app ({base_url} by default):
    make app
    pytest <this file>

Inputs baked in: {supplied}
"""

from __future__ import annotations

import os
import re

from playwright.sync_api import Frame, Page, sync_playwright


def frame(page: Page, name: str) -> Frame:
    """The named frame, re-fetched.

    A `Frame` handle goes stale when its page navigates, so it is looked up at each use
    rather than held.
    """
    found = page.frame(name=name)
    assert found is not None, f"frame {{name!r}} not found"
    return found


BASE_URL = os.environ.get("CUA_BASE_URL", "{base_url}")

# Fixture credentials for the bundled mock application. Signing in is setup, not part of the
# capability -- the artifact begins on the authenticated frameset.
USER = os.environ.get("CUA_MOCK_USERNAME", "teller01")
PASSWORD = os.environ.get("CUA_MOCK_PASSWORD", "not-a-real-password")


def test_{test_name}() -> None:
    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(f"{{BASE_URL}}/login")
            page.fill('input[name="user"]', USER)
            page.fill('input[name="pw"]', PASSWORD)
            page.click('input[type="submit"]')
            page.wait_for_load_state()
            page.goto(f"{{BASE_URL}}/")
            page.wait_for_load_state()

{body}
        finally:
            browser.close()
'''
