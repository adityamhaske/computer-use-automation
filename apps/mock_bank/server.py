"""The hostile-but-bounded mock back-office application.

A stand-in for the kind of system this project exists to automate: a server-rendered,
frameset-based, table-laid-out credit union back-office screen with no API, no test IDs, and no
clean DOM.

**Hostile on purpose.** Framesets, nested tables, labels in adjacent cells rather than
`<label for>`, inline `onclick` navigation, opaque class names, and controls with no accessible
name at all. Naive CSS-selector automation should struggle here; that is the point.

**Bounded on purpose.** Deterministic by construction: no randomness, no timing races, seeded data,
and every exceptional state reachable only by explicitly arming a fault. A flaky fixture makes every
downstream failure ambiguous, and this application is a fixture, not a deliverable.

Run:
    make app                # base tenant   -> http://localhost:8811
    make app-variant-b      # second tenant -> same product, rebranded
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from apps.mock_bank.data import find_member, savings_account
from apps.mock_bank.faults import Fault, FaultState
from apps.mock_bank.variants import get_variant

HERE = Path(__file__).parent
SESSION_COOKIE = "mb_session"
AS_OF = "09/11/2026"  # fixed: a clock in a fixture is nondeterminism

# Obvious fakes. Real credentials never appear in this repo, and the system still treats these as
# secrets -- referenced as {$secret: ...} from artifacts, redacted from every sink.
VALID_USER = "teller01"
VALID_PW = "not-a-real-password"


def create_app(variant_key: str = "base") -> FastAPI:
    variant = get_variant(variant_key)
    app = FastAPI(title=f"mock_bank[{variant_key}]", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")

    # Process-local, deliberately not persisted. See faults.py.
    faults = FaultState()
    app.state.faults = faults
    app.state.variant = variant

    def render(name: str, request: Request, **ctx: Any) -> HTMLResponse:
        ctx.setdefault("v", variant)
        ctx.setdefault("session", request.cookies.get(SESSION_COOKIE))
        return templates.TemplateResponse(request=request, name=name, context=ctx)

    def signed_in(request: Request) -> bool:
        return bool(request.cookies.get(SESSION_COOKIE))

    def guard(request: Request) -> HTMLResponse | RedirectResponse | None:
        """Shared pre-request checks: transient failure, expired session, surprise interstitial.

        Order matters and is fixed. A 502 is checked first because an unreachable server cannot
        also be showing you a login page -- collapsing these would make the fault matrix ambiguous.
        """
        if faults.should_trigger(Fault.TRANSIENT_LOAD):
            return HTMLResponse(
                templates.get_template("unavailable.html").render(v=variant), status_code=502
            )
        if faults.should_trigger(Fault.SESSION_TIMEOUT):
            response = RedirectResponse("/login", status_code=303)
            response.delete_cookie(SESSION_COOKIE)
            return response
        if not signed_in(request):
            return RedirectResponse("/login", status_code=303)
        if faults.should_trigger(Fault.UNDECLARED_DIALOG):
            return HTMLResponse(
                templates.get_template("dialog.html").render(
                    v=variant, next_url=str(request.url.path)
                )
            )
        return None

    # ---------------------------------------------------------------- shell

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        target = "/search" if signed_in(request) else "/login"
        return render("frameset.html", request, content_src=target)

    @app.get("/nav", response_class=HTMLResponse)
    def nav(request: Request) -> HTMLResponse:
        return render("nav.html", request)

    # ---------------------------------------------------------------- auth

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> HTMLResponse:
        return render("login.html", request, error=None)

    @app.post("/login")
    def login_submit(
        request: Request,
        user: Annotated[str, Form()] = "",
        pw: Annotated[str, Form()] = "",
    ) -> Response:
        if user.strip() != VALID_USER or pw != VALID_PW:
            return render("login.html", request, error="Invalid operator ID or password.")
        response = RedirectResponse("/search", status_code=303)
        response.set_cookie(SESSION_COOKIE, user.strip(), httponly=True)
        return response

    @app.get("/logout")
    def logout() -> Response:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    # ---------------------------------------------------------------- flow

    @app.get("/search", response_class=HTMLResponse)
    def search_form(request: Request) -> Response:
        return guard(request) or render("search.html", request, error=None)

    @app.post("/search")
    async def search_submit(request: Request) -> Response:
        if (blocked := guard(request)) is not None:
            return blocked
        # The field name differs per tenant -- that is one of the things that invalidates a
        # recorded CSS hint. Accept whichever this variant renders.
        form = await request.form()
        member_id = str(form.get(variant.field_member_no, "")).strip()
        if not member_id:
            return render("search.html", request, error="Member number is required.")

        member = find_member(member_id)
        if member is None:
            # A BUSINESS OUTCOME. HTTP 200, no error styling -- a legitimate answer, not a crash.
            return render("not_found.html", request, member_id=member_id)
        if member.restricted:
            return render("denied.html", request)
        return RedirectResponse(f"/member/{member.member_id}", status_code=303)

    @app.get("/member/{member_id}", response_class=HTMLResponse)
    def member_detail(request: Request, member_id: str) -> Response:
        if (blocked := guard(request)) is not None:
            return blocked
        member = find_member(member_id)
        if member is None:
            return render("not_found.html", request, member_id=member_id)
        if member.restricted:
            return render("denied.html", request)
        return render(
            "detail.html", request, member=member, savings=savings_account(member), as_of=AS_OF
        )

    @app.get("/member/{member_id}/new-subaccount", response_class=HTMLResponse)
    def subaccount_form(request: Request, member_id: str) -> Response:
        if (blocked := guard(request)) is not None:
            return blocked
        member = find_member(member_id)
        if member is None:
            return render("not_found.html", request, member_id=member_id)
        return render("new_subaccount.html", request, member=member, error=None)

    @app.post("/member/{member_id}/new-subaccount")
    async def subaccount_submit(request: Request, member_id: str) -> Response:
        if (blocked := guard(request)) is not None:
            return blocked
        member = find_member(member_id)
        if member is None:
            return render("not_found.html", request, member_id=member_id)

        form = await request.form()
        acct_type = str(form.get(variant.field_acct_type, "")).strip()
        deposit = str(form.get(variant.field_deposit, "")).strip()

        if faults.should_trigger(Fault.VALIDATION_ERROR):
            return render(
                "new_subaccount.html",
                request,
                member=member,
                error="Initial deposit is below the minimum required for this account type.",
            )
        if not acct_type:
            return render(
                "new_subaccount.html", request, member=member, error="Account type is required."
            )
        if not deposit:
            return render(
                "new_subaccount.html", request, member=member, error="Initial deposit is required."
            )

        # Derived from the inputs rather than random, so a replay's confirmation is reproducible.
        reference = f"SA-{member.member_id}-{acct_type[:3].upper()}"
        return render(
            "confirm.html",
            request,
            member=member,
            acct_type=acct_type,
            deposit=deposit,
            reference=reference,
        )

    # ---------------------------------------------------------------- stubs

    @app.get("/reports", response_class=HTMLResponse)
    def reports(request: Request) -> Response:
        return guard(request) or render("not_found.html", request, member_id="-")

    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request) -> Response:
        return guard(request) or render("not_found.html", request, member_id="-")

    # ------------------------------------------------------------- control
    # Unlinked and unrendered: this never appears in a UiSnapshot, so the agent cannot find it and
    # cannot disarm a fault to make its own life easier. Armed out-of-band before a run.

    @app.post("/_control/arm")
    def control_arm(payload: dict[str, Any]) -> dict[str, Any]:
        faults.arm(str(payload["fault"]), int(payload.get("count", 1)))
        return {"armed": faults.snapshot()}

    @app.post("/_control/reset")
    def control_reset() -> dict[str, Any]:
        faults.reset()
        return {"armed": faults.snapshot()}

    @app.get("/_control/state")
    def control_state() -> dict[str, Any]:
        return {"variant": variant.key, "armed": faults.snapshot()}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock credit union back-office")
    parser.add_argument("--port", type=int, default=8811)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--variant", default="base", choices=["base", "variant_b"])
    args = parser.parse_args()
    uvicorn.run(create_app(args.variant), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
