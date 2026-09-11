"""Marshalling surface calls onto the thread that owns the session.

A constraint that only shows up once a *human* needs to reach the session: the synchronous
Playwright API is bound to the thread that created it, and a web server runs request handlers on a
threadpool. So the obvious console implementation -- a FastAPI endpoint calling `driver.observe()`
-- fails with `greenlet.error: Cannot switch to a different thread`.

It is not a test artifact. Any co-browsing console has this problem: automation owns the browser on
one thread, and an operator arrives on another.

The fix is a single-owner thread. One thread creates the session and is the only one that ever
touches it; everybody else submits a callable and waits for the result. That also happens to give
the session the same single-writer discipline the lease gives it logically -- two operators clicking
at once serialize here, rather than interleaving inside the driver.

**The session must be created on this thread, not merely called from it.** Marshalling calls to a
driver that was constructed elsewhere does not help: the binding is to the constructing thread, so
the proxy would simply be forwarding to the wrong one. A served console therefore starts the thread
first and builds the driver inside it::

    session = SessionThread(); session.start()
    driver = session.call(lambda: PlaywrightCdpDriver(headless=False))

Deliberately tiny and type-agnostic: it runs callables and knows nothing about drivers, so
`cua.hitl` does not have to import `cua.surfaces` to hold one.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")

_STOP = object()


@dataclass
class SessionThread:
    """Owns a live session and executes work on its thread, one call at a time."""

    name: str = "cua-session"
    _queue: queue.Queue[Any] = field(default_factory=queue.Queue, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._serve, name=self.name, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            work, result, *_ = item
            try:
                result.put((True, work()))
            except Exception as exc:
                result.put((False, exc))

    def call(self, work: Callable[[], T], *, timeout: float = 30.0) -> T:
        """Run `work` on the owning thread and return its result.

        An exception raised there is re-raised here, so a caller sees the real failure rather than a
        timeout. Losing the original traceback across threads is the cost; a caller that only ever
        learned "it timed out" would be worse.
        """
        if self._thread is None:
            raise RuntimeError("session thread is not running; call start() first")

        result: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
        self._queue.put((work, result))
        try:
            ok, value = result.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(f"session call did not complete within {timeout}s") from exc

        if not ok:
            raise value
        return value  # type: ignore[no-any-return]

    def stop(self) -> None:
        if self._thread is None:
            return
        self._queue.put(_STOP)
        self._thread.join(timeout=5)
        self._thread = None
