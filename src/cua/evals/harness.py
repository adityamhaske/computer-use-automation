"""Running many replays cheaply, without making them less real.

One browser per suite, a fresh evidence directory per run. The alternative -- a full rig per run --
spends about a second of browser launch on every 400ms replay, which turns a 20-run stability
measurement into a minute of mostly waiting. The alternative in the other direction -- one evidence
bus for every run -- would append every trace into one file and make the determinism comparison
compare a run against itself plus its predecessors.

What is *not* shared is anything that decides: policy, resolver and redaction are rebuilt per run
from the same config (`Rig.rebind_evidence`). A borrowed rig is not a weaker rig, so these runs are
evidence about the same path production uses.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from cua.domain.capability import Capability
from cua.domain.run_record import RunKind, RunRecord
from cua.evidence.record import build_run_record
from cua.replay.executor import ReplayExecutor
from cua.runtime.capture import FailureCapture
from cua.runtime.wiring import Rig, build_rig


@dataclass(frozen=True)
class MockApp:
    """A running instance of the target application."""

    url: str
    variant: str


@contextmanager
def mock_app(variant: str = "base") -> Iterator[MockApp]:
    """Boot the hostile mock back-office in-process on a free port."""
    import uvicorn
    from apps.mock_bank.server import create_app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = uvicorn.Server(
        uvicorn.Config(create_app(variant), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:  # pragma: no cover -- the app failing to boot is not an eval result
        raise RuntimeError("mock app did not start")

    try:
        yield MockApp(url=f"http://127.0.0.1:{port}", variant=variant)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def sign_in(rig: Rig, base_url: str) -> None:
    """Put the session where an operator's session would already be.

    Setup, not measurement: this drives the browser directly because it is the equivalent of a
    fixture. Every action actually being scored goes through the dispatcher.
    """
    from apps.mock_bank.server import VALID_PW, VALID_USER

    page = rig.driver.page
    page.goto(f"{base_url}/login")
    page.fill('input[name="user"]', VALID_USER)
    page.fill('input[name="pw"]', VALID_PW)
    page.click('input[type="submit"]')
    page.wait_for_load_state()
    page.goto(f"{base_url}/")
    page.wait_for_load_state()


@dataclass
class Harness:
    """Replays a capability N times over one live browser, returning the records."""

    base_url: str
    label: str
    headless: bool = True

    def run(
        self, capability: Capability, cases: list[dict[str, str]], *, repeats: int = 1
    ) -> list[RunRecord]:
        """Replay every case `repeats` times and project each run into a `RunRecord`."""
        owner = build_rig(
            run_id=f"eval-{self.label}",
            # `evals-runs`, not `evals`: the reports are the deliverable and belong in a directory
            # a reviewer can open. Per-run traces are working evidence -- dozens of directories that
            # would bury the two files anyone actually reads, so they are gitignored.
            kind="evals-runs",
            headless=self.headless,
            extra_domains=(self.base_url.removeprefix("http://"),),
            allow_vision=False,
        )
        records: list[RunRecord] = []
        try:
            sign_in(owner, self.base_url)
            for index in range(repeats):
                for case in cases:
                    records.append(self._one(owner, capability, case, index))
        finally:
            owner.close()
        return records

    def _one(
        self, owner: Rig, capability: Capability, case: dict[str, str], index: int
    ) -> RunRecord:
        inputs = "-".join(f"{k}{v}" for k, v in sorted(case.items()))
        rig = owner.rebind_evidence(
            run_id=f"eval-{self.label}-{inputs}-{index:02d}", kind="evals-runs"
        )
        result = ReplayExecutor(
            dispatcher=rig.dispatcher,
            evidence=rig.evidence,
            capture=FailureCapture(driver=rig.driver, evidence=rig.evidence),
        ).run(capability, dict(case))
        return build_run_record(rig.run_dir, kind=RunKind.REPLAY, result=result)
