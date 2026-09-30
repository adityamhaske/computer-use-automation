"""Which key configures the model, and what happens when there is none."""

from __future__ import annotations

import pytest

from cua.agent.llm import LlmError, OpenRouterLlm, api_key_from_env


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def test_omniroute_key_wins_over_the_legacy_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "legacy")
    monkeypatch.setenv("OMNIROUTE_API_KEY", "omni")
    assert api_key_from_env() == "omni"


def test_the_legacy_name_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "legacy")
    assert api_key_from_env() == "legacy"


def test_no_key_refuses_to_build_a_client_and_names_both_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    with pytest.raises(LlmError) as excinfo:
        OpenRouterLlm()
    assert "OMNIROUTE_API_KEY" in str(excinfo.value)


def test_blank_env_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env.example` ships the base URL and model blank; blank must mean "use the default"."""
    _clear(monkeypatch)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "omni")
    monkeypatch.setenv("CUA_LLM_BASE_URL", "")
    monkeypatch.setenv("CUA_LLM_MODEL", "")
    llm = OpenRouterLlm()
    assert llm.base_url.startswith("https://")
    assert llm.model
