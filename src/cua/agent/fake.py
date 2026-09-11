"""A scripted `LlmPort`, so CI runs the real discovery loop with no model and no network.

This is what makes the expensive half of the system continuously tested. Everything except the
model's judgement is exercised on every commit: rendering, tool dispatch, descriptor synthesis and
its round-trip check, resolution, policy, the evidence trail, and the stop conditions.

**It picks controls the way a model does.** The script names a control by role and accessible name;
the fake then finds that control's id by reading the *rendered page view* out of the last message --
the same text a real model is shown. It has no privileged access to the snapshot.

That detail matters more than it looks. A fake given the snapshot directly would keep passing even
if rendering broke, silently, and the first sign would be a real discovery run failing for reasons
nothing in CI could explain. Here, a rendering bug fails the suite.

When the script asks for a control that is not on the page, the fake gives up rather than
improvising
-- the same behaviour the system prompt asks of a real model, and it keeps a broken script from
looking like a hung loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from cua.agent.llm import LlmResponse, ToolCall


@dataclass
class ScriptedCall:
    """One step of a recorded flow, described the way a model would perceive it."""

    tool: str
    why: str = "scripted step"
    role: str | None = None
    name: str | None = None
    """Role and accessible name of the control, as rendered. Resolved to an id at call time."""
    occurrence: int | None = None
    """Which match to take when the page genuinely has several.

    Mirrors the resolver's own rule: ambiguity is refused unless something explicit disambiguates
    it. A real model breaks such a tie by looking at context; a script has to say which one it
    means. Leaving this None keeps the refusal, which is what a script *should* hit when it has
    been written against a page that changed.
    """
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeLlm:
    """Replays a script through the real loop."""

    script: list[ScriptedCall]
    model: str = "fake/scripted"
    calls_made: int = 0
    seen_prompts: list[str] = field(default_factory=list)

    @property
    def model_name(self) -> str:
        return self.model

    def complete(
        self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmResponse:
        page_view = self._current_page(messages)
        self.seen_prompts.append(page_view)

        if self.calls_made >= len(self.script):
            return self._give_up("the script is exhausted")

        step = self.script[self.calls_made]
        self.calls_made += 1

        arguments: dict[str, Any] = {"why": step.why, **step.arguments}

        if step.role is not None:
            node_id = find_node_id(page_view, step.role, step.name, occurrence=step.occurrence)
            if node_id is None:
                return self._give_up(f"no unique {step.role} named {step.name!r} on this page")
            arguments["node_id"] = node_id

        return LlmResponse(
            text=step.why,
            tool_calls=(
                ToolCall(id=f"call-{self.calls_made}", name=step.tool, arguments=arguments),
            ),
            prompt_tokens=len(page_view) // 4,
            completion_tokens=20,
            model=self.model,
            finish_reason="tool_calls",
        )

    @staticmethod
    def _current_page(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            content = message.get("content") or ""
            if isinstance(content, str) and content.startswith("CURRENT PAGE"):
                return content
        return ""

    def _give_up(self, reason: str) -> LlmResponse:
        return LlmResponse(
            text=reason,
            tool_calls=(
                ToolCall(
                    id=f"call-{self.calls_made}",
                    name="give_up",
                    arguments={"reason": reason, "why": reason},
                ),
            ),
            model=self.model,
            finish_reason="tool_calls",
        )


def find_node_id(
    page_view: str, role: str, name: str | None, *, occurrence: int | None = None
) -> str | None:
    """Find a control's id in the rendered page, by role and accessible name.

    Reads the same lines a model reads, e.g.::

        [content] textbox "Member Number"   #content:/table[0]/row[1]/cell[1]/textbox[0]

    Returns None when absent, or when several match and no `occurrence` was given -- ambiguity is
    refused here for the same reason the resolver refuses it: silently picking one of several would
    make a scripted run pass in a situation where the real system would have stopped.
    """
    pattern = re.compile(
        r"^\s*(?:\[(?P<frame>[^\]]+)\]\s*)?(?P<role>\w+)(?P<rest>.*?)#(?P<id>\S+)$"
    )
    matches: list[str] = []

    for line in page_view.splitlines():
        found = pattern.match(line)
        if found is None or found.group("role") != role:
            continue
        rest = found.group("rest")
        if name is not None and f'"{name}"' not in rest:
            continue
        matches.append(found.group("id"))

    if occurrence is not None:
        return matches[occurrence] if 0 <= occurrence < len(matches) else None
    return matches[0] if len(matches) == 1 else None
