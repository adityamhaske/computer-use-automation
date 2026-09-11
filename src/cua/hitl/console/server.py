"""A minimal operator console over the `SessionBroker`.

The important thing this file does *not* do: forward input to the page. Every operator gesture
becomes a `RawInput` action submitted to the broker, which runs it through
`Action -> TargetResolver -> PolicyEngine -> SurfaceDriver` under the `HUMAN` profile. If this
server were bypassed tomorrow the guarantees would still hold, because they do not live here.

The live view is a CDP screencast: `Page.startScreencast` emits JPEG frames of the *same* page the
automation was driving, which is what makes this co-browsing rather than a second browser showing
the same URL.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from cua.domain.action import RawInput, RawInputKind
from cua.hitl.broker import SessionBroker
from cua.hitl.session_thread import SessionThread


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

    def on_session(self, work: Callable[[], Any]) -> Any:
        """Run something that touches the surface, on the thread entitled to."""
        return self.session.call(work) if self.session is not None else work()


def create_console(deps: ConsoleDeps) -> FastAPI:
    app = FastAPI(title="cua operator console", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return CONSOLE_HTML

    @app.get("/api/interventions")
    def interventions() -> list[dict[str, Any]]:
        """The queue, oldest first. Each entry is the context card an operator acts on."""
        return [request.context_card() for request in deps.broker.queue.pending()]

    @app.get("/api/state")
    def state() -> dict[str, Any]:
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
        cdp = deps.driver._cdp
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 60, "everyNthFrame": 2})

        def on_frame(event: dict[str, Any]) -> None:
            cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

        cdp.on("Page.screencastFrame", on_frame)
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
                        "frame": base64.b64encode(deps.on_session(deps.driver.screenshot)).decode(),
                    }
                )
        except WebSocketDisconnect:
            pass
        finally:
            cdp.send("Page.stopScreencast")

    return app


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


CONSOLE_HTML = """\
<!doctype html>
<title>cua operator console</title>
<style>
  body { font: 13px system-ui, sans-serif; margin: 0; background: #14161a; color: #e6e6e6; }
  header { padding: 10px 14px; background: #1e2228; display: flex; gap: 18px; align-items: center; }
  .pill { padding: 2px 8px; border-radius: 10px; background: #2c333c; }
  main { display: grid; grid-template-columns: 340px 1fr; height: calc(100vh - 44px); }
  aside { padding: 12px; overflow: auto; border-right: 1px solid #2c333c; }
  .card { background: #1e2228; padding: 10px; border-radius: 6px; margin-bottom: 10px; }
  .card b { color: #8fc7ff; }
  button { background: #2f6feb; color: #fff; border: 0; padding: 6px 10px; border-radius: 4px;
           cursor: pointer; }
  #screen { width: 100%; height: 100%; object-fit: contain; background: #000; }
  #log { position: fixed; bottom: 0; right: 0; max-width: 50%; padding: 8px;
         background: rgba(0,0,0,.7); font-family: ui-monospace, monospace; font-size: 11px; }
</style>
<header>
  <b>Operator console</b>
  <span class="pill" id="state">…</span>
  <span class="pill" id="epoch"></span>
  <button onclick="release()">Hand back to automation</button>
</header>
<main>
  <aside id="queue"></aside>
  <div><img id="screen" onclick="click_at(event)"></div>
</main>
<div id="log"></div>
<script>
  let ws;
  const log = m => document.getElementById('log').textContent = m;

  async function refresh() {
    const s = await (await fetch('/api/state')).json();
    document.getElementById('state').textContent = s.state + ' · ' + s.holder;
    document.getElementById('epoch').textContent = 'epoch ' + s.epoch;
    const q = await (await fetch('/api/interventions')).json();
    document.getElementById('queue').innerHTML = q.length ? q.map(card).join('') :
      '<div class=card>No open interventions.</div>';
  }
  const card = c => `<div class=card>
      <b>${c.capability}</b><br>${c.goal || ''}<br><br>
      stopped at <b>${c.stopped_at || '?'}</b><br>${c.because}<br><br>
      <button onclick="claim('${c.intervention}')">Take control</button></div>`;

  async function claim(id) {
    await fetch('/api/claim/' + id, {method: 'POST'});
    ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = e => {
      const d = JSON.parse(e.data);
      if (d.frame) document.getElementById('screen').src = 'data:image/png;base64,' + d.frame;
      if (d.status && d.status !== 'ok') log(d.status + ': ' + (d.message || ''));
      if (d.error) log(d.error);
    };
    refresh();
  }
  function click_at(e) {
    if (!ws) return;
    const r = e.target.getBoundingClientRect();
    ws.send(JSON.stringify({kind: 'mouse_click',
      x: Math.round((e.clientX - r.left) * (e.target.naturalWidth / r.width)),
      y: Math.round((e.clientY - r.top) * (e.target.naturalHeight / r.height))}));
  }
  document.addEventListener('keydown', e => {
    if (!ws || e.key.length !== 1) return;
    ws.send(JSON.stringify({kind: 'text', text: e.key}));
  });
  async function release() {
    const r = await (await fetch('/api/release', {method: 'POST'})).json();
    log('handed back — ' + r.human_delta);
    ws && ws.close(); ws = null;
    refresh();
  }
  refresh(); setInterval(refresh, 3000);
</script>
"""
