"""Assembling a runnable system from configuration.

One place where the pieces are wired together, so the CLI, the tests and the demo all get the same
object graph. A second wiring path is a second chance for a guardrail to be left out of one of them.

Lives in `cua.runtime` because constructing a driver means importing one, and only `cua.runtime`
may do that. It was written under `cua.cli` first and
`tests/invariants/test_policy_chokepoint.py` caught it -- the second time that test has found a
violation the import-linter contract did not.

The tempting argument is that *constructing* a driver is not *acting* on a surface, so the CLI
could be exempt. It was declined for the same reason as the `evidence/capture.py` case in Phase 05:
each exemption is defensible alone, and together they turn a rule you can check into a rule you have
to argue about. Moving a module is cheaper than that.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cua.evidence.bus import EvidenceBus
from cua.policy.config import PolicyConfig, parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.redact import Redactor
from cua.policy.secrets import SecretResolver
from cua.runtime.dispatcher import Dispatcher
from cua.surfaces.playwright_cdp.driver import PlaywrightCdpDriver
from cua.targeting.resolver import TargetResolver

DEFAULT_POLICY = Path("config/policy.yaml")
EVIDENCE_ROOT = Path("evidence")


@dataclass
class Rig:
    """Everything a run needs, wired once."""

    dispatcher: Dispatcher
    driver: PlaywrightCdpDriver
    evidence: EvidenceBus
    redactor: Redactor
    secrets: SecretResolver
    config: PolicyConfig
    run_dir: Path

    owns_driver: bool = True
    """False for a rig that borrows another's browser. `close()` is then a no-op, so a borrowed rig
    cannot shut down a session its owner is still using."""

    def close(self) -> None:
        if self.owns_driver:
            self.driver.close()

    def rebind_evidence(self, *, run_id: str, kind: str) -> Rig:
        """A second rig over the *same* live browser, writing to a fresh evidence directory.

        Measuring stability means replaying the same capability many times, and a browser launch
        costs more than the replay does. But the runs must stay separately evidenced: the trace is
        opened for append, so sharing one bus would concatenate every run into a single file and
        make the determinism comparison meaningless -- it would be comparing a run against itself
        plus its predecessors.

        Everything that decides anything -- policy, resolver, redaction -- is rebuilt from the same
        config, so a borrowed rig is not a weaker rig. Only the evidence sink differs.
        """
        run_dir = fresh_run_dir(kind, run_id)
        evidence = EvidenceBus(run_dir, self.redactor, run_id=run_id)
        return Rig(
            dispatcher=Dispatcher(
                driver=self.driver,
                policy=PolicyEngine(self.config),
                resolver=TargetResolver(allow_vision=False),
                evidence=evidence,
                secrets=self.secrets,
            ),
            driver=self.driver,
            evidence=evidence,
            redactor=self.redactor,
            secrets=self.secrets,
            config=self.config,
            run_dir=run_dir,
            owns_driver=False,
        )


def fresh_run_dir(kind: str, run_id: str) -> Path:
    """An empty evidence directory for this run.

    Cleared, not merely created. The trace is opened for append, so reusing a run id without
    clearing silently concatenates this run onto the last one -- and the result still looks like a
    valid trace, just with more steps than actually happened. That mistake has now been made twice:
    once in `make demo`, and once in the eval harness, where it surfaced as four cases reported
    non-deterministic when replay had in fact decided identically every time.

    A run id identifies one run. Enforcing that here rather than at each call site is the difference
    between a rule and a habit.
    """
    run_dir = EVIDENCE_ROOT / kind / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def load_policy(path: Path | None = None) -> PolicyConfig:
    resolved = path or Path(os.environ.get("CUA_POLICY_FILE", DEFAULT_POLICY))
    return parse_policy(resolved.read_text(encoding="utf-8"))


def build_rig(
    *,
    run_id: str,
    kind: str,
    headless: bool | None = None,
    policy_path: Path | None = None,
    extra_domains: tuple[str, ...] = (),
    allow_vision: bool = False,
) -> Rig:
    """Wire a live rig.

    `allow_vision` is the one asymmetry between discovery and replay: discovery may fall back to a
    vision rung, replay may not, because pixel coordinates are not reproducible. Everything else --
    policy, resolver, evidence, redaction -- is identical, which is what makes a discovery run
    evidence about the production path rather than about a parallel one.
    """
    config = load_policy(policy_path)
    if extra_domains:
        allowlist = config.allowlist.model_copy(
            update={"domains": (*config.allowlist.domains, *extra_domains)}
        )
        config = config.model_copy(update={"allowlist": allowlist})

    redactor = Redactor(config.redaction)
    secrets = SecretResolver(redactor=redactor)

    run_dir = fresh_run_dir(kind, run_id)
    evidence = EvidenceBus(run_dir, redactor, run_id=run_id)

    if headless is None:
        headless = os.environ.get("CUA_HEADLESS", "true").lower() != "false"

    driver = PlaywrightCdpDriver(headless=headless, session_id=run_id)
    dispatcher = Dispatcher(
        driver=driver,
        policy=PolicyEngine(config),
        resolver=TargetResolver(allow_vision=allow_vision),
        evidence=evidence,
        secrets=secrets,
    )
    return Rig(
        dispatcher=dispatcher,
        driver=driver,
        evidence=evidence,
        redactor=redactor,
        secrets=secrets,
        config=config,
        run_dir=run_dir,
    )


@dataclass
class SupervisedSession:
    """A live session an operator can take over, wired for the console."""

    broker: Any
    driver: Any
    session: Any
    evidence: EvidenceBus
    run_dir: Path

    dispatcher: Dispatcher | None = None
    """The same chokepoint the broker routes operator input through.

    Exposed so the console can run a real capability against this session -- without it the only
    reference is `broker.dispatch`, typed `object`, and an escalation could never be produced on the
    surface an operator is watching.
    """

    redactor: Redactor | None = None
    config: PolicyConfig | None = None

    def close(self) -> None:
        self.session.call(self.driver.close)
        self.session.stop()


def build_supervised_session(
    *, run_id: str, target: str, headless: bool = True, policy_path: Path | None = None
) -> SupervisedSession:
    """Wire a session the operator console can supervise.

    Lives here because it constructs a driver, and only `cua.runtime` may do that. The CLI asked to
    build one directly first, and `tests/invariants/test_policy_chokepoint.py` caught it -- the
    third time that scan has found a violation the import-linter contract did not.

    The session is created **on its owning thread**: synchronous Playwright binds to the thread that
    constructs it, and the console serves requests from a threadpool. Building the browser here on
    the calling thread and marshalling to another would forward to the wrong one.
    """
    from cua.hitl.broker import SessionBroker
    from cua.hitl.session_thread import SessionThread
    from cua.surfaces.playwright_cdp.driver import PlaywrightCdpDriver

    config = load_policy(policy_path)
    redactor = Redactor(config.redaction)
    run_dir = fresh_run_dir("escalation", run_id)
    evidence = EvidenceBus(run_dir, redactor, run_id=run_id)

    thread = SessionThread(name="cua-console-session")
    thread.start()

    def boot() -> PlaywrightCdpDriver:
        return PlaywrightCdpDriver(headless=headless, session_id=run_id)

    driver = thread.call(boot)
    dispatcher = Dispatcher(
        driver=driver,
        policy=PolicyEngine(config),
        resolver=TargetResolver(allow_vision=False),
        evidence=evidence,
        secrets=SecretResolver(redactor=redactor),
    )
    # Opened after the dispatcher exists, so the navigation is allowlist-checked and recorded
    # rather than driven straight off the raw page handle.
    thread.call(lambda: dispatcher.open_entrypoint(target, session_id=run_id))
    broker = SessionBroker(session_id=run_id, evidence=evidence, dispatch=dispatcher)
    return SupervisedSession(
        broker=broker,
        driver=driver,
        session=thread,
        evidence=evidence,
        run_dir=run_dir,
        dispatcher=dispatcher,
        redactor=redactor,
        config=config,
    )
