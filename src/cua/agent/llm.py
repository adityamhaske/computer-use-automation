"""The model port, and an OpenAI-compatible client for it.

Deliberately thin. The loop depends on `LlmPort`, not on a vendor SDK, for three reasons that all
matter here:

- **CI must be able to run the real loop with no model.** `FakeLlm` implements this same port by
  replaying a recorded transcript, so every policy check, resolution and evidence write is exercised
  for free on every commit.
- **Model choice is configuration.** OpenRouter fronts many providers behind one OpenAI-compatible
  endpoint, so swapping models for an eval is an env var rather than a code change.
- **The surface is small enough to reason about.** One method, one response shape. A vendor SDK
  brings retry policies, streaming and telemetry we would then have to audit for whether they leak
  prompt content.

Nothing here retries on a *model* error. A model that refuses or returns nonsense is a signal about
the run, not a transient fault, and hiding it behind a retry would corrupt the transcript that the
artifact is compiled from.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class ToolCall:
    """One action the model wants to take, drawn from the closed action space."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LlmResponse:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    finish_reason: str = ""

    provider: str = ""
    """Which upstream the gateway actually routed to, as the gateway reported it."""

    request_id: str = ""
    """The gateway's own id for this exchange.

    Recorded because it is the one part of a run record a scripted stand-in cannot invent: the
    model name is whatever the caller asked for, but a request id is issued by something else and
    can be looked up in the gateway's logs afterwards. Evidence that only quotes itself is weak
    evidence, and the brief's one non-negotiable is that the discovery run was real.
    """

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LlmPort(Protocol):
    """What the discovery loop needs from a model, and nothing more."""

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LlmResponse: ...

    @property
    def model_name(self) -> str: ...


class LlmError(RuntimeError):
    """The model could not be reached or replied unusably."""


@dataclass
class OpenRouterLlm:
    """An OpenAI-compatible chat-completions client.

    Works against OpenRouter, a self-hosted gateway, or anything else that speaks the same
    /chat/completions shape.
    """

    api_key: str = field(default_factory=lambda: os.environ.get("OPENROUTER_API_KEY", ""))
    base_url: str = field(
        default_factory=lambda: os.environ.get("CUA_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    )
    model: str = field(
        default_factory=lambda: os.environ.get("CUA_LLM_MODEL", "anthropic/claude-sonnet-4.5")
    )
    timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("CUA_LLM_TIMEOUT_S", "120"))
    )
    temperature: float = 0.0
    """Zero by default. Discovery is allowed to be probabilistic, but there is no upside to extra
    variance when the goal is to find one working path through a form."""

    def __post_init__(self) -> None:
        if not self.api_key:
            raise LlmError(
                "OPENROUTER_API_KEY is not set. Discovery needs a model; replay, escalation and "
                "the whole test suite do not -- see README."
            )

    @property
    def model_name(self) -> str:
        return self.model

    def complete(
        self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmResponse:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": self.temperature,
        }
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as exc:
            raise LlmError(f"could not reach the model: {exc}") from exc

        if response.status_code >= 400:
            # The body can echo the prompt, so only the status and a short excerpt are surfaced.
            raise LlmError(f"model returned {response.status_code}: {response.text[:200]}")

        return _parse(response.json(), response.headers)


def _parse(body: dict[str, Any], headers: Mapping[str, str] | None = None) -> LlmResponse:
    choices = body.get("choices") or []
    if not choices:
        raise LlmError("model returned no choices")

    message = choices[0].get("message", {})
    usage = body.get("usage", {})

    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function", {})
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            # Malformed arguments are a real signal about the run, not something to paper over.
            # The loop sees a tool call it cannot execute and tells the model so.
            arguments = {"__malformed__": function.get("arguments", "")}
        calls.append(
            ToolCall(id=raw.get("id", ""), name=function.get("name", ""), arguments=arguments)
        )

    return LlmResponse(
        text=message.get("content") or "",
        tool_calls=tuple(calls),
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=int(usage.get("completion_tokens", 0)),
        model=body.get("model", ""),
        finish_reason=choices[0].get("finish_reason", ""),
        # OpenAI-compatible gateways vary in what they expose; absent headers simply mean the
        # fields stay empty rather than the parse failing.
        provider=_header(headers, "x-omniroute-provider", "x-provider", "openai-processing-ms"),
        request_id=_header(headers, "x-omniroute-request-id", "x-request-id", "request-id"),
    )


def _header(headers: Mapping[str, str] | None, *names: str) -> str:
    """The first of `names` the response actually carried."""
    if not headers:
        return ""
    for name in names:
        value = headers.get(name)
        if value:
            return str(value)
    return ""
