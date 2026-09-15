"""A minimal operator console over the `SessionBroker`.

The important thing this file does *not* do: forward input to the page. Every operator gesture
becomes a `RawInput` action submitted to the broker, which runs it through
`Action -> TargetResolver -> PolicyEngine -> SurfaceDriver` under the `HUMAN` profile. If this
server were bypassed tomorrow the guarantees would still hold, because they do not live here.

The live view is a still frame of the *same* page the automation was driving, re-captured after
every policed gesture -- which is what makes this co-browsing rather than a second browser showing
the same URL. Continuous streaming is a documented cut (REPORT.md §7).
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from cua.catalog.store import CapabilityNotFoundError, CapabilityStore
from cua.domain.action import RawInput, RawInputKind
from cua.domain.capability import Capability
from cua.domain.run_record import RunKind
from cua.evidence.record import build_run_record
from cua.hitl.broker import SessionBroker
from cua.hitl.session_thread import SessionThread
from cua.policy.config import parse_policy
from cua.runtime.capture import FailureCapture

STATIC_DIR = Path(__file__).resolve().parent / "static"

# URL segment -> the directory it reads under evidence_root, and the RunKind to derive a record
# under (only used as a fallback when a run has no committed run_record.json yet).
RUN_KIND_DIRS: dict[str, RunKind] = {
    "discovery": RunKind.DISCOVERY,
    "replay": RunKind.REPLAY,
    "escalation": RunKind.INTERVENTION,
}


@dataclass
class ConsoleDeps:
    """What the console needs. Injected so the server owns no state of its own."""

    broker: SessionBroker
    driver: Any
    """The `PlaywrightCdpDriver`. Needed for the screencast, which is a view concern -- the console
    never dispatches through it."""

    session: SessionThread | None = None
    """The thread that owns the live session.

    Required in a served console and optional in tests. Synchronous Playwright is bound to its
    creating thread, and a web server handles requests on a threadpool -- so every call that touches
    the surface has to be marshalled back. Without this, the first operator click fails with
    `greenlet.error: Cannot switch to a different thread`.
    """

    supervising: Capability | None = None
    """The capability currently running on this session, if any.

    Carried so the live view can honour its `sensitive` declarations -- the only signal that can
    locate regulated data in a screenshot.
    """

    catalog_root: Path = field(default_factory=lambda: Path("evidence/capabilities"))
    """Where the Capabilities page reads the catalog from. Read-only -- the console never writes
    here, matching `CapabilityStore`'s own contract (list/resolve/load, never save)."""

    evidence_root: Path = field(default_factory=lambda: Path("evidence"))
    """Where the Runs and Evidence pages read committed run evidence from. Read-only."""

    policy_path: Path = field(
        default_factory=lambda: Path(os.environ.get("CUA_POLICY_FILE", "config/policy.yaml"))
    )
    """What the Settings page displays. Read-only -- a UI settings toggle can never write here;
    see `/api/settings`."""

    def on_session(self, work: Callable[[], Any]) -> Any:
        """Run something that touches the surface, on the thread entitled to."""
        return self.session.call(work) if self.session is not None else work()


def create_console(deps: ConsoleDeps) -> FastAPI:
    app = FastAPI(title="cua operator console", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def no_heuristic_caching(request: Request, call_next: Any) -> Any:
        """`StaticFiles` sends `ETag`/`Last-Modified` but no `Cache-Control`, which leaves a
        browser free to serve a page, script, or stylesheet from its own heuristic cache without
        ever revalidating -- so an edit here can silently not appear for a viewer who already
        loaded the console once. `no-cache` still lets the browser keep a local copy; it just makes
        every load a conditional GET (a 304 when nothing changed), which is effectively free."""
        response = await call_next(request)
        if request.method == "GET" and not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text("utf-8")

    @app.get("/api/interventions")
    def interventions() -> list[dict[str, Any]]:
        """The queue, oldest first. Each entry is the context card an operator acts on."""
        return [request.context_card() for request in deps.broker.queue.pending()]

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        # Reclaim a lapsed operator hold here rather than on a timer. The UI polls this every couple
        # of seconds, so a session an operator walked away from returns to the queue within one poll
        # of its deadline -- without adding a scheduler to a single-process system.
        deps.on_session(deps.broker.sweep)
        lease = deps.broker.lease
        return {
            "session": lease.session_id,
            "state": lease.state.value,
            "holder": lease.holder.value,
            "epoch": lease.epoch,
            "operator": lease.operator,
            "pending": len(deps.broker.queue.pending()),
        }

    @app.post("/api/claim/{intervention_id}")
    def claim(intervention_id: str, operator: str = "operator") -> dict[str, Any]:
        epoch = deps.broker.claim(intervention_id, operator)
        return {"epoch": epoch, "state": deps.broker.lease.state.value}

    @app.post("/api/release")
    def release() -> dict[str, Any]:
        delta = deps.on_session(lambda: deps.broker.release(snapshot_after=deps.driver.observe()))
        epoch = deps.broker.resume()
        return {
            "epoch": epoch,
            "state": deps.broker.lease.state.value,
            "human_delta": delta.summary() if delta else "not captured",
        }

    @app.websocket("/ws")
    async def live(socket: WebSocket) -> None:
        """Frames out, operator gestures in.

        Gestures arrive as messages and leave as `RawInput` actions through the broker. A refusal
        is sent back to the operator rather than swallowed: being told "that navigation is outside
        the allowlist" is far more useful than a click that silently does nothing.
        """
        await socket.accept()

        # Send the current screen before waiting for input. Without this the operator's first click
        # is blind: the viewport stays empty until a gesture comes back with a frame attached, so
        # the only way to see the page was to act on it first -- which is precisely backwards for a
        # console whose job is deciding what to do about a stuck run.
        #
        # This used to start `Page.startScreencast` instead. The frames were acknowledged and then
        # dropped on the floor -- `on_frame` did nothing but ack -- so the page paid for a
        # continuous JPEG stream and showed none of it. A still frame per gesture is what the
        # console actually renders, and saying so is better than a screencast that only looks
        # like one. Continuous streaming remains a documented cut (REPORT.md §7).
        await socket.send_json({"frame": _frame(deps), "status": "connected"})
        try:
            while True:
                message = await socket.receive_json()
                action = _to_action(message)
                if action is None:
                    await socket.send_json(
                        {"error": f"unsupported gesture {message.get('kind')!r}"}
                    )
                    continue
                # Bound explicitly rather than closed over: the lambda runs immediately here, but
                # a loop variable captured by reference is one refactor away from dispatching the
                # wrong operator gesture.
                outcome = deps.on_session(partial(deps.broker.human_action, action))
                await socket.send_json(
                    {
                        "status": outcome.status.value,
                        "message": outcome.message,
                        "frame": _frame(deps),
                    }
                )
        except WebSocketDisconnect:
            pass

    # ------------------------------------------------------- read-only additions
    #
    # Everything below is new surface for the redesigned console (Runs, Capabilities, Evidence,
    # Settings pages). All of it is read-only: it projects data the system already produces on
    # disk (`CapabilityStore`, `build_run_record`, `policy.yaml`) rather than adding a second way
    # to write it, so the UI cannot drift from -- or bypass -- what those modules already enforce.

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        """Every run under `evidence_root`, newest first. The Runs page's table."""
        out: list[dict[str, Any]] = []
        for kind_name, run_kind in RUN_KIND_DIRS.items():
            base = deps.evidence_root / kind_name
            if not base.exists():
                continue
            for run_dir in sorted(base.iterdir()):
                if not run_dir.is_dir() or not (run_dir / "trace.jsonl").exists():
                    continue
                record = _read_or_build_record(run_dir, run_kind)
                if record is None:
                    continue
                result = record.get("result") or {}
                out.append(
                    {
                        "run_id": record.get("run_id", run_dir.name),
                        "kind": kind_name,
                        "capability_ref": record.get("capability_ref"),
                        "goal": record.get("goal"),
                        "status": result.get("status"),
                        "started_at": record.get("started_at"),
                        "duration_ms": record.get("duration_ms"),
                        "steps": len(record.get("steps") or []),
                        "drift_score": result.get("drift_score", 0.0),
                        "human_actions": record.get("human_actions", 0),
                        "evidence_ref": record.get("evidence_ref") or str(run_dir),
                    }
                )
        out.sort(key=lambda r: r.get("started_at") or "", reverse=True)
        return out

    @app.get("/api/runs/{kind}/{run_id}")
    def run_detail(kind: str, run_id: str) -> dict[str, Any]:
        """The full record plus the raw trace and evidence file listing. The Runs/Evidence detail
        view."""
        run_dir = _run_dir(deps, kind, run_id)
        record = _read_or_build_record(run_dir, RUN_KIND_DIRS[kind])
        if record is None:
            raise HTTPException(404, "run has no evidence")
        trace_path = run_dir / "trace.jsonl"
        events = (
            [
                json.loads(line)
                for line in trace_path.read_text("utf-8").splitlines()
                if line.strip()
            ]
            if trace_path.exists()
            else []
        )
        snapshots = (
            sorted(p.name for p in (run_dir / "snapshots").glob("*.json"))
            if (run_dir / "snapshots").exists()
            else []
        )
        screenshots = (
            sorted(p.name for p in (run_dir / "screenshots").glob("*.png"))
            if (run_dir / "screenshots").exists()
            else []
        )
        return {
            "record": record,
            "events": events,
            "snapshots": snapshots,
            "screenshots": screenshots,
        }

    @app.get("/api/runs/{kind}/{run_id}/file/{name:path}")
    def run_file(kind: str, run_id: str, name: str) -> Any:
        """One evidence file (trace or a saved snapshot), parsed. Confined to the run's own
        directory -- `name` is caller-supplied, so it is resolved and checked against it before
        anything is read."""
        run_dir = _run_dir(deps, kind, run_id)
        target = (run_dir / name).resolve()
        if run_dir != target and run_dir not in target.parents:
            raise HTTPException(404, "file not found")
        if not target.is_file():
            raise HTTPException(404, "file not found")
        if target.suffix == ".jsonl":
            return [
                json.loads(line) for line in target.read_text("utf-8").splitlines() if line.strip()
            ]
        if target.suffix == ".json":
            return json.loads(target.read_text("utf-8"))
        raise HTTPException(415, f"unsupported evidence file type: {target.suffix}")

    @app.get("/api/runs/{kind}/{run_id}/screenshot/{name}")
    def run_screenshot(kind: str, run_id: str, name: str) -> FileResponse:
        run_dir = _run_dir(deps, kind, run_id)
        shots = (run_dir / "screenshots").resolve()
        target = (shots / name).resolve()
        if shots not in target.parents or not target.is_file():
            raise HTTPException(404, "screenshot not found")
        return FileResponse(target, media_type="image/png")

    @app.get("/api/capabilities")
    def list_capabilities() -> list[dict[str, Any]]:
        """The catalog, as the Capabilities page's list. Same source `cua catalog list` reads."""
        store = CapabilityStore(root=deps.catalog_root)
        evals = _load_evals(deps.evidence_root)
        out = []
        for entry in store.list():
            cap = entry.capability
            out.append(
                {
                    "ref": entry.ref,
                    "id": cap.id,
                    "version": cap.version,
                    "title": cap.title,
                    "description": cap.description,
                    "state": entry.state,
                    "signature": entry.signature,
                    "surface": {
                        "vendor": cap.surface.app.vendor,
                        "product": cap.surface.app.product,
                        "version_range": cap.surface.app.version_range,
                    },
                    "inputs": len(cap.inputs),
                    "outputs": len(cap.outputs),
                    "steps": len(cap.steps),
                    "outcomes": [o.code for o in cap.outcomes],
                    "max_risk": cap.max_risk.value,
                    "stability": evals.get(entry.ref),
                }
            )
        for path, why in store.unreadable:
            out.append({"ref": path.name, "state": "unreadable", "error": why})
        return out

    @app.get("/api/capabilities/{ref:path}")
    def capability_detail(ref: str) -> dict[str, Any]:
        """The full artifact -- typed inputs/outputs/steps/policy/recovery/provenance -- the way
        the Capabilities detail view makes the schema visible to a reviewer."""
        store = CapabilityStore(root=deps.catalog_root)
        try:
            entry = store.resolve(ref)
        except CapabilityNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        cap = entry.capability
        return {
            "capability": cap.model_dump(mode="json"),
            "state": entry.state,
            "tool_schema": cap.tool_schema(),
            "stability": _load_evals(deps.evidence_root).get(entry.ref, {}),
        }

    @app.get("/api/settings")
    def settings() -> dict[str, Any]:
        """A read-only snapshot for the Settings page. The UI can never write through this path --
        there is no corresponding POST, and nothing here feeds back into `PolicyEngine`."""
        policy = None
        if deps.policy_path.exists():
            policy = parse_policy(deps.policy_path.read_text("utf-8")).model_dump(mode="json")
        return {
            "policy_file": str(deps.policy_path),
            "policy": policy,
            "llm": {
                "base_url": os.environ.get("CUA_LLM_BASE_URL", "https://openrouter.ai/api/v1"),
                "model": os.environ.get("CUA_LLM_MODEL", "anthropic/claude-sonnet-4.5"),
                "api_key_configured": bool(os.environ.get("OPENROUTER_API_KEY")),
                "max_steps": int(os.environ.get("CUA_LLM_MAX_STEPS", "40")),
                "max_tokens": int(os.environ.get("CUA_LLM_MAX_TOKENS", "200000")),
                "timeout_s": float(os.environ.get("CUA_LLM_TIMEOUT_S", "120")),
            },
            "session": {
                "session_id": deps.broker.session_id,
                "default_hold_minutes": 30,
            },
        }

    # Static assets last: every explicit route above wins on an exact match; anything unmatched
    # (styles/*, pages/*, app.js, ...) falls through to the files on disk.
    app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")

    return app


def _frame(deps: ConsoleDeps) -> str:
    """The current screen, as a base64 PNG, captured on the thread that owns the session.

    Redacted the same way an evidence screenshot is: the capability under supervision declares which
    fields are `sensitive`, `FailureCapture.sensitive_regions` turns those declarations into
    geometry, and the driver paints over them *before the bytes exist*. This used to call
    `screenshot()` bare, so the operator's live view was the one sink in the system that showed
    regulated data in the clear -- and it is the sink most likely to be on a second monitor in an
    open-plan office.

    A screenshot cannot be redacted by pattern matching after the fact, so with no capability in
    play there is nothing to locate and the frame is sent unredacted. That is why the console is
    only ever pointed at a supervised run.
    """
    capability = deps.supervising
    if capability is None:
        return base64.b64encode(deps.on_session(deps.driver.screenshot)).decode()

    def grab() -> bytes:
        snapshot = deps.driver.observe()
        regions = FailureCapture(
            driver=deps.driver, evidence=deps.broker.evidence
        ).sensitive_regions(snapshot, capability)
        shot: bytes = deps.driver.screenshot(redact=regions)
        return shot

    return base64.b64encode(deps.on_session(grab)).decode()


def _to_action(message: dict[str, Any]) -> RawInput | None:
    """One operator gesture as a policed action."""
    kind = message.get("kind")
    if kind not in {k.value for k in RawInputKind}:
        return None
    return RawInput(
        kind=RawInputKind(kind),
        x=message.get("x"),
        y=message.get("y"),
        key=message.get("key"),
        text=message.get("text"),
    )


def _run_dir(deps: ConsoleDeps, kind: str, run_id: str) -> Path:
    """Resolve `kind`/`run_id` to a directory under `evidence_root`, refusing anything that would
    escape it. `run_id` is caller-supplied (it comes off the URL), so this is the one seam a path
    traversal attempt would go through."""
    if kind not in RUN_KIND_DIRS:
        raise HTTPException(404, f"unknown run kind {kind!r}")
    base = (deps.evidence_root / kind).resolve()
    run_dir = (base / run_id).resolve()
    if base != run_dir and base not in run_dir.parents:
        raise HTTPException(404, "run not found")
    if not run_dir.is_dir():
        raise HTTPException(404, "run not found")
    return run_dir


def _read_or_build_record(run_dir: Path, kind: RunKind) -> dict[str, Any] | None:
    """The committed `run_record.json` if one exists, else the same projection `cua eval` and the
    CLI use, built live from `trace.jsonl` (see `cua.evidence.record.build_run_record`). Never a
    third, UI-only shape -- the record IS `RunRecord`, serialized."""
    record_path = run_dir / "run_record.json"
    if record_path.exists():
        try:
            return dict(json.loads(record_path.read_text("utf-8")))
        except Exception:
            pass
    try:
        return build_run_record(run_dir, kind=kind).model_dump(mode="json")
    except Exception:
        return None


def _load_evals(evidence_root: Path) -> dict[str, dict[str, Any]]:
    """`evidence/evals/*.evaluation.json`, keyed by the capability ref each measured. Backs the
    "stability score" shown on the Capabilities page -- the same numbers `make eval` writes."""
    out: dict[str, dict[str, Any]] = {}
    evals_dir = evidence_root / "evals"
    if not evals_dir.exists():
        return out
    for path in evals_dir.glob("*.evaluation.json"):
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        ref = data.get("capability_ref")
        if ref:
            out.setdefault(ref, {})[path.stem.replace(".evaluation", "")] = data
    return out
