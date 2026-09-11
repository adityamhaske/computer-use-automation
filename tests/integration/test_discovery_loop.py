"""The discovery loop, end to end, against the real app in a real browser.

No model: a scripted `FakeLlm` drives the *real* loop, so everything except the model's judgement is
exercised -- rendering, tool dispatch, descriptor synthesis and its round-trip check, resolution,
policy, evidence, and the stop conditions.

That makes the expensive half of the system continuously tested for free. The live-model run
(`tests/e2e/`) then has to prove only one thing: that a model can navigate this surface. Everything
around it is already covered here.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from apps.mock_bank.server import VALID_PW, VALID_USER, create_app

from cua.agent.fake import FakeLlm, ScriptedCall
from cua.agent.loop import DiscoveryAgent
from cua.agent.stop import Budget, StopReason
from cua.domain.target import ResolutionStrategy
from cua.evidence.bus import EventType, EvidenceBus
from cua.policy.config import parse_policy
from cua.policy.engine import PolicyEngine
from cua.policy.redact import Redactor
from cua.runtime.dispatcher import Dispatcher
from cua.surfaces.playwright_cdp.driver import PlaywrightCdpDriver
from cua.targeting.resolver import TargetResolver

pytestmark = [pytest.mark.browser, pytest.mark.slow]

POLICY = Path(__file__).resolve().parents[2] / "config/policy.yaml"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Server:
    def __init__(self, variant: str = "base") -> None:
        self.port = _free_port()
        self._server = uvicorn.Server(
            uvicorn.Config(create_app(variant), host="127.0.0.1", port=self.port, log_level="error")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> _Server:
        self._thread.start()
        for _ in range(100):
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("mock app did not start")

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@pytest.fixture(scope="module")
def app() -> Iterator[_Server]:
    with _Server() as server:
        yield server


@pytest.fixture
def rig(app: _Server, tmp_path: Path) -> Iterator[tuple]:
    """A signed-in browser, wired through the real chokepoint.

    The allowlist is widened to this test's ephemeral port -- the shipped policy names the default
    ports, and a test that silently disabled the allowlist would be testing a system nobody runs.
    """
    config = parse_policy(POLICY.read_text())
    allowlist = config.allowlist.model_copy(
        update={"domains": (*config.allowlist.domains, f"127.0.0.1:{app.port}")}
    )
    config = config.model_copy(update={"allowlist": allowlist})

    driver = PlaywrightCdpDriver(headless=True)
    page = driver.page
    page.goto(f"{app.url}/login")
    page.fill('input[name="user"]', VALID_USER)
    page.fill('input[name="pw"]', VALID_PW)
    page.click('input[type="submit"]')
    page.wait_for_load_state()
    page.goto(f"{app.url}/")
    page.wait_for_load_state()

    redactor = Redactor(config.redaction)
    bus = EvidenceBus(tmp_path, redactor, run_id="disc-test")
    dispatcher = Dispatcher(
        driver=driver,
        policy=PolicyEngine(config),
        resolver=TargetResolver(allow_vision=True),
        evidence=bus,
    )
    try:
        yield dispatcher, bus, redactor
    finally:
        driver.close()


def _agent(rig: tuple, script: list[ScriptedCall], **budget: int) -> tuple[DiscoveryAgent, FakeLlm]:
    dispatcher, bus, redactor = rig
    llm = FakeLlm(script=script)
    agent = DiscoveryAgent(
        llm=llm,
        dispatcher=dispatcher,
        evidence=bus,
        redactor=redactor,
        budget=Budget(**budget) if budget else Budget(),
    )
    return agent, llm


SAVINGS_FLOW = [
    ScriptedCall(
        "type_text",
        "enter the member number",
        role="textbox",
        name="Member Number",
        arguments={"text": "12345"},
    ),
    ScriptedCall("click", "run the search", role="button", name="Search"),
    # The balance appears twice on this page -- once in the accounts grid, once in the summary
    # row beside its label. The fake refuses that ambiguity unless told which, exactly as the
    # resolver would; occurrence=1 is the summary row a real model would read.
    ScriptedCall(
        "extract",
        "read the balance",
        role="cell",
        name="$4,210.55",
        occurrence=1,
        arguments={"output_name": "savings_balance"},
    ),
    ScriptedCall(
        "finish",
        "done",
        arguments={
            "summary": "Read member 12345's savings balance",
            "checkpoint": "Member Detail heading with a currency-formatted savings balance",
        },
    ),
]


# --------------------------------------------------------------- the thread


def test_the_loop_completes_a_real_goal(rig: tuple) -> None:
    """Goal in, working flow out. The thread the whole project exists to demonstrate."""
    agent, _ = _agent(rig, SAVINGS_FLOW)
    run = agent.run(
        goal="Look up member 12345 and read their current savings balance",
        target_url=rig[0].driver.session_info().url,
    )

    assert run.succeeded, f"stopped at {run.stop_reason}: {run.summary}"
    assert run.stop_reason is StopReason.GOAL_MET
    assert run.outputs["savings_balance"] == "$4,210.55"
    assert run.checkpoint_hint, "the model must say what proves the goal was reached"


def test_every_step_synthesized_a_descriptor_that_resolves(rig: tuple) -> None:
    """Descriptors are verified by use, while the run is still live.

    The model points at a node it can see; we describe that node semantically; the resolver then has
    to find the same node from the description alone. A failure here would otherwise surface as a
    capability that breaks on its very first replay, weeks later.
    """
    agent, _ = _agent(rig, SAVINGS_FLOW)
    run = agent.run(goal="Read the savings balance for member 12345", target_url="")

    acted = [step for step in run.effective_steps if step.descriptor is not None]
    assert acted, "no step produced a descriptor"
    for step in acted:
        assert step.descriptor_verified, f"{step.tool} on {step.node_id} did not round-trip"


def test_recorded_strategies_become_the_drift_baseline(rig: tuple) -> None:
    """Each step records the rung that resolved it, which is what later runs are compared
    against."""
    agent, _ = _agent(rig, SAVINGS_FLOW)
    run = agent.run(goal="Read the savings balance for member 12345", target_url="")

    strategies = {step.tool: step.strategy for step in run.effective_steps}
    assert strategies["type_text"] is ResolutionStrategy.SEMANTIC_EXACT
    assert strategies["click"] is ResolutionStrategy.SEMANTIC_EXACT


# ------------------------------------------------------ the same guardrails


def test_discovery_actions_pass_through_the_chokepoint(rig: tuple) -> None:
    """The guarantee that makes a discovery run meaningful: it is not a privileged path.

    Every action the model took was authorized by the same engine a replay uses, and the evidence
    can prove it.
    """
    _, bus, _ = rig
    agent, _ = _agent(rig, SAVINGS_FLOW)
    agent.run(goal="Read the savings balance for member 12345", target_url="")

    events = bus.read_events()
    dispatches = [e for e in events if e["event"] == EventType.DISPATCH.value]
    assert dispatches, "nothing was dispatched"
    assert bus.unauthorized_dispatches() == []


def test_the_model_is_shown_a_snapshot_not_raw_html(rig: tuple) -> None:
    """It reasons in the vocabulary the artifact stores, which is what makes a trace compilable."""
    agent, llm = _agent(rig, SAVINGS_FLOW)
    agent.run(goal="Read the savings balance for member 12345", target_url="")

    page_view = llm.seen_prompts[0]
    assert "<table" not in page_view and "<input" not in page_view
    assert 'textbox "Member Number"' in page_view
    assert "#" in page_view, "controls must be addressable"


def test_llm_calls_are_recorded_for_the_artifact_to_reference(rig: tuple) -> None:
    """§3.2: the artifact is decoupled from the transcript but must be able to point at it."""
    _, bus, _ = rig
    agent, _ = _agent(rig, SAVINGS_FLOW)
    agent.run(goal="Read the savings balance for member 12345", target_url="")

    calls = [e for e in bus.read_events() if e["event"] == EventType.LLM_CALL.value]
    assert len(calls) >= len(SAVINGS_FLOW)
    assert all("prompt_tokens" in call for call in calls)


# --------------------------------------------------------------- stopping


def test_a_model_that_asks_for_a_missing_control_gives_up(rig: tuple) -> None:
    """Preferred over guessing, and the same behaviour the system prompt asks for."""
    agent, _ = _agent(
        rig,
        [
            ScriptedCall(
                "click", "click something that is not there", role="button", name="Nonexistent"
            )
        ],
    )
    run = agent.run(goal="impossible", target_url="")
    assert run.stop_reason is StopReason.GAVE_UP


def test_max_steps_bounds_the_run(rig: tuple) -> None:
    """Budgets are hard. A loop that politely suggests a model wind down spends the budget
    anyway."""
    looping = [ScriptedCall("click", "keep clicking", role="button", name="Search")] * 10
    agent, _ = _agent(rig, looping, max_steps=3)
    run = agent.run(goal="loop forever", target_url="")

    assert run.stop_reason is StopReason.MAX_STEPS
    assert len(run.steps) <= 4


def test_a_dead_end_is_detected(rig: tuple) -> None:
    """Repeated actions that change nothing. The loop cannot tell a stuck model from a slow one, so
    it bounds the attempt rather than diagnosing it."""
    stuck = [
        ScriptedCall(
            "type_text",
            "type the same thing",
            role="textbox",
            name="Member Number",
            arguments={"text": "12345"},
        )
    ] * 8
    agent, _ = _agent(rig, stuck, max_steps=20, max_unchanged_steps=3)
    run = agent.run(goal="go nowhere", target_url="")

    assert run.stop_reason is StopReason.DEAD_END


def test_effective_steps_exclude_the_wandering(rig: tuple) -> None:
    """A discovery run wanders; a capability should not reproduce the wandering.

    Only steps that actually did something are candidates for compilation.
    """
    with_a_miss = [
        ScriptedCall("click", "a control that is not here", role="button", name="Ghost"),
        *SAVINGS_FLOW,
    ]
    agent, _ = _agent(rig, with_a_miss)
    run = agent.run(goal="Read the savings balance for member 12345", target_url="")

    # The bad step ends the run (give_up), so this asserts the weaker, still-useful property:
    # nothing that failed is ever counted as effective.
    assert all(step.ok for step in run.effective_steps)
    assert all(step.tool not in ("finish", "give_up") for step in run.effective_steps)
