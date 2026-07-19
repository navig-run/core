"""
Regression (config#1): configuring `agent.fallback_chain` must NOT strip
effort/thinking params + cost tracking from every call.

Old behavior: when a fallback chain existed, run_llm dispatched via
_call_with_fallback (which carries neither thinking_params nor cost_tracker), so
merely SETTING agent.fallback_chain silently degraded reasoning quality and
under-counted spend on the primary model. Now the primary always goes through
_call_and_wrap (thinking + cost), and the fallback chain is used only if the
primary actually errors.
"""

from __future__ import annotations

import pytest

from navig.llm import generate as g
from navig.llm.types import LLMResult


def _ok(**kw):
    sel = kw["selection"]
    return LLMResult(content="primary-ok", model=sel.model_name,
                     provider=sel.provider_name, finish_reason="stop")


@pytest.fixture
def _spy(monkeypatch):
    calls = {"wrap_kwargs": None, "fallback_called": False}

    def wrap(**kw):
        calls["wrap_kwargs"] = kw
        return _ok(**kw)

    def fb(**kw):
        calls["fallback_called"] = True
        return LLMResult(content="fallback", model="fb", provider="fb", finish_reason="stop")

    monkeypatch.setattr(g, "_call_and_wrap", wrap)
    monkeypatch.setattr(g, "_call_with_fallback", fb)
    monkeypatch.setattr(g, "_load_fallback_chain", lambda: ["openai:gpt-4o"])
    return calls


def test_primary_keeps_thinking_and_cost_with_fallback_configured(_spy):
    res = g.run_llm([{"role": "user", "content": "hi"}], effort="high")
    assert res.content == "primary-ok"                 # primary used, not fallback
    assert _spy["fallback_called"] is False            # fallback NOT engaged (primary ok)
    # the primary dispatch received thinking_params + cost_tracker + turn kwargs
    assert "thinking_params" in _spy["wrap_kwargs"]
    assert "cost_tracker" in _spy["wrap_kwargs"]
    assert "turn" in _spy["wrap_kwargs"]


def test_fallback_engages_only_on_primary_error(monkeypatch):
    calls = {"fallback_called": False}

    def wrap(**kw):
        return LLMResult(content="", model=kw["selection"].model_name,
                         provider=kw["selection"].provider_name,
                         finish_reason="error:Boom")

    def fb(**kw):
        calls["fallback_called"] = True
        return LLMResult(content="recovered", model="fb", provider="fb", finish_reason="stop")

    monkeypatch.setattr(g, "_call_and_wrap", wrap)
    monkeypatch.setattr(g, "_call_with_fallback", fb)
    monkeypatch.setattr(g, "_load_fallback_chain", lambda: ["openai:gpt-4o"])

    res = g.run_llm([{"role": "user", "content": "hi"}], effort="high")
    assert calls["fallback_called"] is True            # primary errored → fallback engaged
    assert res.content == "recovered"
