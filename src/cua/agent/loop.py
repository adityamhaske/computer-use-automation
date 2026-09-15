"""The discovery loop -- the probabilistic half of the system.

A model observes a normalized snapshot, chooses one action from a closed set, and the result is fed
back. It runs until the goal is met or a budget stops it.

Everything the model asks for travels the same path a replay would:

    tool call -> descriptor synthesis -> TargetResolver -> PolicyEngine -> SurfaceDriver

So the guardrails exercised here are the guardrails that run in production, and a discovery run that
completes is evidence that the *system* can perform the flow -- not just that a model could talk
about it.

One property worth calling out: each synthesized descriptor is **verified by use**. The model points
at a node it can see; we describe that node semantically; the resolver then has to find the same
node
from the description alone. A mismatch means the description is wrong, and it is caught here --
while
a model is still in the loop and the run can adapt -- rather than at the first replay weeks later.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from cua.agent.llm import LlmError, LlmPort, ToolCall
from cua.agent.prompts.system import SYSTEM_PROMPT, goal_message
from cua.agent.render import ACTIONABLE, render_snapshot
from cua.agent.stop import Budget
from cua.agent.tools import TOOL_NAMES, TOOLS
from cua.domain.action import Action, Click, Navigate, PressKey, Select, Type
from cua.domain.actor import Actor
from cua.domain.discovery import DiscoveryRun, DiscoveryStep, StopReason
from cua.domain.run_record import RunKind
from cua.domain.snapshot import UiSnapshot
from cua.domain.target import TargetDescriptor
from cua.evidence.bus import EventType, EvidenceBus
from cua.evidence.record import build_run_record, write_run_record
from cua.policy.redact import Redactor
from cua.runtime.dispatcher import Dispatcher, DispatchStatus
from cua.targeting.synthesize import synthesize_descriptor

MAX_HISTORY_TURNS = 12


@dataclass
class DiscoveryAgent:
    """Runs one goal against one live surface."""

    llm: LlmPort
    dispatcher: Dispatcher
    evidence: EvidenceBus
    redactor: Redactor
    budget: Budget = field(default_factory=Budget)
    session_id: str = "discovery"
    lease_epoch: int = 1

    def run(self, *, goal: str, target_url: str) -> DiscoveryRun:
        # The evidence bus already has a run id -- the one naming the directory this run writes to.
        # Minting a second one here meant the compiled artifact's `provenance.discovered_by.run_id`
        # named a run that exists nowhere on disk, while `transcript_ref` pointed at a different
        # directory. Provenance that cannot be followed back to its own evidence is decoration.
        run_id = getattr(self.evidence, "run_id", "") or f"disc-{uuid.uuid4().hex[:10]}"
        self.evidence.emit(
            EventType.RUN_START,
            actor=Actor.AUTOMATION,
            kind="discovery",
            goal=goal,
            target_url=target_url,
            model=self.llm.model_name,
        )

        history: list[dict[str, Any]] = []
        steps: list[DiscoveryStep] = []
        outputs: dict[str, str] = {}
        stop_reason = StopReason.MAX_STEPS
        summary = ""
        checkpoint_hint = ""

        while True:
            if (exceeded := self.budget.exceeded()) is not None:
                stop_reason = exceeded
                break

            snapshot = self.dispatcher.observe()
            page_view = render_snapshot(snapshot, redactor=self.redactor)

            try:
                response = self.llm.complete(
                    messages=self._messages(goal, target_url, history, page_view), tools=TOOLS
                )
            except LlmError as exc:
                self.evidence.emit(EventType.NOTE, note="model error", error=str(exc))
                stop_reason = StopReason.ERROR
                break

            self.evidence.emit(
                EventType.LLM_CALL,
                actor=Actor.AUTOMATION,
                model=response.model or self.llm.model_name,
                # Gateway-issued, so a reader can check this run against the gateway's own logs.
                provider=response.provider or None,
                request_id=response.request_id or None,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                reasoning=response.text[:500],
                tool_calls=[call.name for call in response.tool_calls],
            )

            if not response.tool_calls:
                # No action proposed. One nudge, then treat it as a dead end -- a model that will
                # not act is not going to start after the third ask.
                history.append({"role": "assistant", "content": response.text or "(no action)"})
                history.append(
                    {
                        "role": "user",
                        "content": "Choose exactly one tool call, or call give_up with a reason.",
                    }
                )
                self.budget.record_step(
                    tokens=response.total_tokens, progressed=False, denied=False
                )
                continue

            call = response.tool_calls[0]

            if call.name == "finish":
                summary = str(call.arguments.get("summary", ""))
                checkpoint_hint = str(call.arguments.get("checkpoint", ""))
                steps.append(
                    DiscoveryStep(
                        index=len(steps),
                        tool="finish",
                        why=str(call.arguments.get("why", "")),
                        arguments=call.arguments,
                        ok=True,
                    )
                )
                stop_reason = StopReason.GOAL_MET
                break

            if call.name == "give_up":
                summary = str(call.arguments.get("reason", ""))
                stop_reason = StopReason.GAVE_UP
                break

            step, feedback, denied = self._perform(call, snapshot, len(steps), outputs)
            steps.append(step)

            after = self.dispatcher.observe() if step.ok else snapshot
            page_changed = after.url != snapshot.url or len(after.nodes) != len(snapshot.nodes)
            # An extraction is a *read*. It cannot change the page, and it is the goal rather than
            # a detour -- a flow whose whole purpose is to read three fields off one screen would
            # otherwise look like three steps of getting nowhere and trip the dead-end detector on
            # its way to succeeding. Progress is "the page moved, or we learned something".
            progressed = page_changed or step.extracted is not None

            history.append(self._assistant_turn(response.text, call))
            history.append({"role": "tool", "tool_call_id": call.id, "content": feedback})

            self.budget.record_step(
                tokens=response.total_tokens, progressed=progressed, denied=denied
            )

        run = DiscoveryRun(
            run_id=run_id,
            goal=goal,
            target_url=target_url,
            stop_reason=stop_reason,
            steps=steps,
            outputs=outputs,
            summary=summary,
            checkpoint_hint=checkpoint_hint,
            budget=self.budget.summary(),
            model=self.llm.model_name,
        )
        self.evidence.emit(
            EventType.RUN_END,
            actor=Actor.AUTOMATION,
            stop_reason=stop_reason.value,
            steps_attempted=len(steps),
            steps_effective=len(run.effective_steps),
            outputs=sorted(outputs),
            budget=self.budget.summary(),
        )
        # A discovery run gets a record too, for the same reason a replay does: the provenance of
        # an artifact is only checkable if the run that produced it left one. `tokens_used` is the
        # field that distinguishes a genuine model-driven run from a scripted one, which matters
        # because the artifact's credibility rests on how it was discovered.
        try:
            write_run_record(
                build_run_record(
                    self.evidence.run_dir,
                    kind=RunKind.DISCOVERY,
                    goal=goal,
                    model_name=self.llm.model_name,
                ),
                self.evidence.run_dir,
                redactor=self.evidence.redactor,
                sensitive_keys=self.evidence.sensitive_keys,
            )
        except OSError as exc:  # pragma: no cover -- evidence must not mask the run's own outcome
            self.evidence.emit(
                EventType.NOTE, actor=Actor.SYSTEM, note=f"run record not written: {exc}"
            )
        return run

    # ------------------------------------------------------------- messages

    def _messages(
        self, goal: str, target_url: str, history: list[dict[str, Any]], page_view: str
    ) -> list[dict[str, Any]]:
        """System prompt, goal, a bounded slice of history, then the CURRENT page.

        The page view appears exactly once, at the end, always fresh. Carrying every past page view
        would blow the token budget on a frameset app and -- worse -- let the model act on a stale
        id it can still see in the transcript.
        """
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": goal_message(goal, target_url)},
            *history[-MAX_HISTORY_TURNS:],
            {"role": "user", "content": f"CURRENT PAGE\n\n{page_view}"},
        ]

    @staticmethod
    def _assistant_turn(text: str, call: ToolCall) -> dict[str, Any]:
        import json

        return {
            "role": "assistant",
            "content": text or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
            ],
        }

    # -------------------------------------------------------------- acting

    def _perform(
        self,
        call: ToolCall,
        snapshot: UiSnapshot,
        index: int,
        outputs: dict[str, str],
    ) -> tuple[DiscoveryStep, str, bool]:
        """Translate one tool call into a policed action. Returns the step, feedback, and whether
        it was denied."""
        why = str(call.arguments.get("why", ""))
        step = DiscoveryStep(
            index=index, tool=call.name, why=why, arguments=call.arguments, url_before=snapshot.url
        )

        if call.name not in TOOL_NAMES:
            step.detail = f"unknown tool {call.name!r}"
            return step, step.detail, False

        # `navigate` is the one action with no on-page target.
        if call.name == "navigate":
            outcome = self.dispatcher.execute(
                Navigate(url=str(call.arguments.get("url", ""))),
                snapshot=snapshot,
                actor=Actor.AUTOMATION,
                session_id=self.session_id,
                lease_epoch=self.lease_epoch,
            )
            step.ok = outcome.ok
            step.detail = outcome.message or outcome.status.value
            return (
                step,
                self._feedback(outcome.ok, step.detail),
                outcome.status is DispatchStatus.DENIED,
            )

        node_id = str(call.arguments.get("node_id", ""))
        node = snapshot.node(node_id)
        if node is None:
            # The model named a control that is not on the page it was just shown -- a truncated
            # id, or one carried over from an earlier snapshot.
            #
            # This branch used to be the run's one silent exit. It dispatched nothing, so the
            # trace recorded an `llm_call` followed by an `observe` and no action between them,
            # and the feedback was a bare "no control with id X" -- which tells the model that it
            # was wrong but not what would be right. A model given only that repeats itself, and
            # three repeats is a dead end: a run that failed for a legible reason, recorded as if
            # nothing had happened.
            #
            # So say what is actually there. The ids below come from the same snapshot the model
            # was shown, so a correction is available without another observe, and the note puts
            # the dead end in the evidence where it can be read back afterwards.
            step.detail = f"no control with id {node_id!r} on the current page"
            self.evidence.emit(
                EventType.NOTE,
                note="tool call named a control that is not in the current snapshot",
                node_id=node_id,
                tool=call.name,
            )
            return step, step.detail + _available(snapshot), False

        step.node_id = node_id
        synthesis = synthesize_descriptor(node, snapshot)
        step.descriptor = synthesis.descriptor
        step.strategy = synthesis.strategy
        step.descriptor_verified = self._verify(synthesis.descriptor, snapshot, node_id)

        if not step.descriptor_verified:
            # The description we would record does not find the node it describes, so recording it
            # produces an artifact that fails on its first replay.
            #
            # The note went to the evidence bus and nowhere else: the one party who could pick a
            # different control -- the model, mid-run -- was never told. It is appended to the tool
            # result below so the next turn can act on it, which is the only moment the choice is
            # still open. Not raised: a control that resists description is often still the right
            # one, and refusing the step would strand a run that is otherwise going fine.
            self.evidence.emit(
                EventType.NOTE,
                note="descriptor synthesis did not round-trip",
                node_id=node_id,
                descriptor=synthesis.descriptor.describe(),
            )
        warning = (
            ""
            if step.descriptor_verified
            else (
                f"\n\nWARNING: this control cannot be described in a way that finds it again "
                f"({synthesis.descriptor.describe()}). A capability recorded from it will fail on "
                f"replay. Prefer a control with a distinct accessible name, or one with a clear "
                f"label beside it, if either will do."
            )
        )

        # `extract` reads from the snapshot; it never touches the surface.
        if call.name == "extract":
            name = str(call.arguments.get("output_name", "value"))
            value = node.value or node.name or ""
            outputs[name] = value
            step.ok = True
            step.extracted = (name, value)
            step.detail = f"recorded {name}"
            return step, f"Recorded {name} = {self.redactor.text(value)}{warning}", False

        action = self._action_for(call, synthesis.descriptor)
        if action is None:
            step.detail = f"{call.name} is not executable"
            return step, step.detail + warning, False

        outcome = self.dispatcher.execute(
            action,
            snapshot=snapshot,
            actor=Actor.AUTOMATION,
            session_id=self.session_id,
            lease_epoch=self.lease_epoch,
        )
        step.ok = outcome.ok
        step.detail = outcome.message or outcome.status.value
        denied = outcome.status is DispatchStatus.DENIED
        return step, self._feedback(outcome.ok, step.detail) + warning, denied

    def _verify(self, descriptor: TargetDescriptor, snapshot: UiSnapshot, node_id: str) -> bool:
        """Does the description we would record actually find the node it describes?"""
        from cua.targeting.resolver import TargetResolutionError

        try:
            return self.dispatcher.resolver.resolve(descriptor, snapshot).node.node_id == node_id
        except TargetResolutionError:
            return False

    @staticmethod
    def _action_for(call: ToolCall, target: TargetDescriptor) -> Action | None:
        arguments = call.arguments
        if call.name == "click":
            return Click(target=target)
        if call.name == "type_text":
            return Type(target=target, value=str(arguments.get("text", "")))
        if call.name == "select_option":
            return Select(target=target, value=str(arguments.get("value", "")))
        if call.name == "press_key":
            return PressKey(key=str(arguments.get("key", "Enter")), target=target)
        return None

    @staticmethod
    def _feedback(ok: bool, detail: str) -> str:
        """What the model is told about its own action.

        A refusal is reported plainly, with the reason. A model that is told *why* it was blocked
        can try a legitimate alternative; one that just sees failure tends to retry the same thing.
        """
        if ok:
            return "Done. The updated page follows."
        return f"That did not work: {detail}"


def _available(snapshot: UiSnapshot, limit: int = 12) -> str:
    """The ids of controls that *are* on this page, for a tool call that named one that is not.

    Bounded on purpose: the point is to give the next turn a correction it can act on, not to
    re-send the page it was already shown. Actionable roles only -- offering text nodes as
    alternatives would invite a click on something that cannot be clicked.
    """
    usable = [n for n in snapshot.nodes if n.role in ACTIONABLE]
    if not usable:
        return "\n\nThere are no actionable controls in this snapshot."

    listed = "\n".join(
        f"  {n.role} {n.name!r}   #{n.node_id}" if n.name else f"  {n.role}   #{n.node_id}"
        for n in usable[:limit]
    )
    more = f"\n  … and {len(usable) - limit} more" if len(usable) > limit else ""
    return f"\n\nControls on this page:\n{listed}{more}"
