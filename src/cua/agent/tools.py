"""The action space, as tool definitions the model may call.

A closed set. The model cannot invent an action, which bounds the blast radius of a confused or
prompt-injected model to what is enumerated here -- and lets the policy engine reason exhaustively
rather than having an "other" case to fall through.

Two design choices worth noting:

**Targets are `node_id`s from the snapshot the model was just shown, not descriptions.** Asking a
model to author a selector or a descriptor invites it to invent one that looks plausible and matches
nothing. Pointing at something it can see is unambiguous, and `synthesize_descriptor` converts that
pick into a durable description afterwards -- so what lands in the artifact is derived from a node
that provably existed.

**Every action carries `why`.** Brief §3.5 asks for a log of what the agent did *and why*; taking
the reason as a required argument means the trace has it for every step without a second call to
ask.
"""

from __future__ import annotations

from typing import Any

_WHY = {
    "type": "string",
    "description": "One short sentence: why this action advances the goal.",
}


def _tool(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {**properties, "why": _WHY},
                "required": [*required, "why"],
                "additionalProperties": False,
            },
        },
    }


_NODE = {
    "type": "string",
    "description": "The #id of a control from the page view. Must be one you can currently see.",
}


TOOLS: list[dict[str, Any]] = [
    _tool("click", "Click a control.", {"node_id": _NODE}, ["node_id"]),
    _tool(
        "type_text",
        "Type into a field. Replaces any existing value.",
        {"node_id": _NODE, "text": {"type": "string"}},
        ["node_id", "text"],
    ),
    _tool(
        "select_option",
        "Choose an option in a dropdown, by visible text or value.",
        {"node_id": _NODE, "value": {"type": "string"}},
        ["node_id", "value"],
    ),
    _tool(
        "press_key",
        "Press a key, optionally after focusing a control.",
        {"key": {"type": "string", "description": "e.g. Enter, Tab, Escape"}, "node_id": _NODE},
        ["key"],
    ),
    _tool(
        "navigate",
        "Go to a URL. Blocked unless the URL is inside the configured allowlist.",
        {"url": {"type": "string"}},
        ["url"],
    ),
    _tool(
        "extract",
        "Record a value from the page as one of the capability's outputs.",
        {
            "node_id": _NODE,
            "output_name": {
                "type": "string",
                "description": "A short snake_case name, e.g. savings_balance.",
            },
        },
        ["node_id", "output_name"],
    ),
    _tool(
        "finish",
        "The goal is achieved. Call this only once the page actually shows the result.",
        {
            "summary": {"type": "string", "description": "What was accomplished."},
            "checkpoint": {
                "type": "string",
                "description": (
                    "What is visible on this page that proves the goal was reached. Be specific: "
                    "a heading and a concrete value, not 'the page loaded'."
                ),
            },
        },
        ["summary", "checkpoint"],
    ),
    _tool(
        "give_up",
        "The goal cannot be achieved from here. Preferred over guessing.",
        {"reason": {"type": "string"}},
        ["reason"],
    ),
]

TOOL_NAMES: frozenset[str] = frozenset(tool["function"]["name"] for tool in TOOLS)
TERMINAL_TOOLS: frozenset[str] = frozenset({"finish", "give_up"})
