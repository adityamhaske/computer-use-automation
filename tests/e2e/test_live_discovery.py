"""One genuine LLM-driven run against a live surface.

Brief §4 is explicit that this is the only thing that cannot be mocked: "at least one genuine
LLM-driven run against a live surface, with the evidence in /evidence/ to show it happened."

Deselected by default (`-m 'not live'`) and skipped without an API key, so the suite stays free and
offline. CI deliberately configures no key: if any other test needed a model, CI should fail rather
than silently skip.

Everything around the model is already covered by `tests/integration/test_discovery_loop.py`, which
runs this same loop with a scripted port. What this test adds is the one thing that harness cannot:
evidence that a model can actually navigate a hostile legacy surface it has never seen.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from apps.mock_bank.server import VALID_PW, VALID_USER, create_app

from cua.agent.llm import OpenRouterLlm
from cua.agent.loop import DiscoveryAgent
from cua.agent.stop import Budget, StopReason
from cua.evidence.bus import EvidenceBus
from cua.policy.config import parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.redact import Redactor
from cua.runtime.dispatcher import Dispatcher
from cua.surfaces.playwright_cdp.driver import PlaywrightCdpDriver
from cua.targeting.resolver import TargetResolver

pytestmark = [
    pytest.mark.live,
    pytest.mark.browser,
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="needs OPENROUTER_API_KEY; every other test runs without a model",
    ),
]

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"
GOAL = "Look up member 12345 and read their current savings balance."


@pytest.fixture(scope="module")
def app() -> Iterator[tuple[str, int]]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])

    server = uvicorn.Server(
        uvicorn.Config(create_app("base"), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", port
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_a_real_model_completes_a_real_goal(app: tuple[str, int], tmp_path: Path) -> None:
    base_url, port = app
    config = parse_policy(POLICY.read_text())
    config = config.model_copy(
        update={
            "allowlist": config.allowlist.model_copy(
                update={"domains": (*config.allowlist.domains, f"127.0.0.1:{port}")}
            )
        }
    )

    redactor = Redactor(config.redaction)
    evidence = EvidenceBus(tmp_path, redactor, run_id="live-discovery")
    driver = PlaywrightCdpDriver(headless=True)

    try:
        page = driver.page
        page.goto(f"{base_url}/login")
        page.fill('input[name="user"]', VALID_USER)
        page.fill('input[name="pw"]', VALID_PW)
        page.click('input[type="submit"]')
        page.wait_for_load_state()
        page.goto(f"{base_url}/")
        page.wait_for_load_state()

        agent = DiscoveryAgent(
            llm=OpenRouterLlm(),
            dispatcher=Dispatcher(
                driver=driver,
                policy=PolicyEngine(config),
                resolver=TargetResolver(allow_vision=True),
                evidence=evidence,
            ),
            evidence=evidence,
            redactor=redactor,
            budget=Budget(max_steps=25, max_tokens=150_000),
        )
        run = agent.run(goal=GOAL, target_url=base_url)
    finally:
        driver.close()

    assert run.stop_reason is StopReason.GOAL_MET, f"{run.stop_reason}: {run.summary}"

    # The model was asked for a balance; it must actually have recorded one.
    assert run.outputs, "no outputs were extracted"
    assert any("4,210.55" in value for value in run.outputs.values()), run.outputs

    # Every action a model took went through the same chokepoint a replay uses.
    assert evidence.unauthorized_dispatches() == []

    # And every description it produced resolves back to the node it described.
    assert all(step.descriptor_verified for step in run.effective_steps if step.descriptor)
