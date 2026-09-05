"""
Regression (config#1): configuring `agent.fallback_chain` must NOT strip
effort/thinking params + cost tracking from every call.

Old behavior: when a fallback chain existed, run_llm dispatched via
_call_with_fallback (which carries neither thinking_params nor cost_tracker), so
merely SETTING agent.fallback_chain silently degraded reasoning quality and
under-counted spend on the primary model. Now the primary always goes through
_call_and_wrap (thinking + cost), and the fallback chain is used only if the
primary actually errors.

**The primary model must be pinned.** `run_llm(...)` with no ``mode`` /
``model_override`` resolves through ``navig.llm.router.resolve_llm``, which reads
the ``LLMModeRouter`` **singleton** — i.e. the operator's own routing config.
`_assemble_fallback_chain` then drops any candidate equal to the primary, so if
routing resolves to ``openai:gpt-4o`` — the very entry these tests configure as
the chain — the chain assembles EMPTY and no fallback can engage.
`test_fallback_engages_only_on_primary_error` failed exactly that way
(`assert False is True`, over a log line reading
``primary openai:gpt-4o failed (unknown: Boom) — no fallback available``) in
combined runs while passing in isolation, which reads like flake. It was routing
resolving differently once a leaked test fake made every provider look available
(fixed separately in #825); the dependency itself is pinned here so it cannot
come back through any other door.

(The other process-global in this path, ``fallback_policy._cooldowns``, is
already reset around every test by this package's ``conftest.py`` — don't
duplicate that here.)
"""

from __future__ import annotations

import pytest

from navig.llm import generate as g
from navig.llm.types import LLMResult

# A primary that is deliberately NOT the configured fallback entry, so
# `_assemble_fallback_chain` can never filter the chain down to empty.
_PRIMARY_PROVIDER = "anthropic"
_PRIMARY_MODEL = "claude-test-primary"
_FALLBACK_SPEC = "openai:gpt-4o"


@pytest.fixture(autouse=True)
def _pin_primary_model(monkeypatch):
    """Pin what `run_llm` routes to, so the fallback chain is never filtered empty.

    Without this these tests read the operator's live routing config — see the
    module docstring. `generate.run_llm` imports `resolve_llm` inside the
    function, so patching it on `navig.llm.router` is what takes effect.
    """
    from navig.llm.router import ResolvedLLMConfig

    def _fixed_resolve(mode=None, user_input=None, prefer_uncensored=None):
        return ResolvedLLMConfig(
            provider=_PRIMARY_PROVIDER,
            model=_PRIMARY_MODEL,
            mode="big_tasks",
            resolution_reason="pinned by test fixture",
        )

    monkeypatch.setattr("navig.llm.router.resolve_llm", _fixed_resolve)


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
