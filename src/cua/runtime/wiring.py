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

    def close(self) -> None:
        self.driver.close()


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
    secrets = SecretResolver()

    run_dir = EVIDENCE_ROOT / kind / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
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
    run_dir = EVIDENCE_ROOT / "escalation" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    evidence = EvidenceBus(run_dir, redactor, run_id=run_id)

    thread = SessionThread(name="cua-console-session")
    thread.start()

    def boot() -> PlaywrightCdpDriver:
        instance = PlaywrightCdpDriver(headless=headless, session_id=run_id)
        instance.page.goto(target, wait_until="load")
        return instance

    driver = thread.call(boot)
    broker = SessionBroker(
        session_id=run_id,
        evidence=evidence,
        dispatch=Dispatcher(
            driver=driver,
            policy=PolicyEngine(config),
            resolver=TargetResolver(allow_vision=False),
            evidence=evidence,
        ),
    )
    return SupervisedSession(
        broker=broker, driver=driver, session=thread, evidence=evidence, run_dir=run_dir
    )
