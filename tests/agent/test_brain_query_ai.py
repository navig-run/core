"""Regression: the Brain's _query_ai reaches an LLM.

It imported `get_ai_client` from `navig.ai` — a module that does not exist — so
`_ai_client` was always None and `_query_ai` always returned None: `navig agent`
ran an autonomous loop whose Brain never used AI (only fallback replies). It now
routes through the canonical `navig.llm.run_llm` seam (system + user messages).
"""

from __future__ import annotations

import types

import navig.llm.generate as gen_mod
from navig.agent.brain import Brain


class _Result:
    def __init__(self, content):
        self.content = content


def _brain():
    # _query_ai uses only these attributes; skip full Component construction.
    b = Brain.__new__(Brain)
    b._ai_client = True
    b._soul = None
    b.agent_config = None
    b.config = types.SimpleNamespace(max_tokens=256, temperature=0.5)
    return b


async def test_query_ai_routes_through_run_llm(monkeypatch):
    captured = {}

    def fake_run_llm(messages, **kw):
        captured["messages"] = messages
        captured["kw"] = kw
        return _Result("  brain says hi  ")

    monkeypatch.setattr(gen_mod, "run_llm", fake_run_llm)

    out = await _brain()._query_ai("do a thing", "SYSTEM METRICS")

    assert out == "brain says hi"  # content, stripped
    assert [m["role"] for m in captured["messages"]] == ["system", "user"]
    # system message carries the default brain prompt
    assert Brain.DEFAULT_SYSTEM_PROMPT.split("\n")[0] in captured["messages"][0]["content"]
    # user message carries the built context + the prompt
    assert "SYSTEM METRICS" in captured["messages"][1]["content"]
    assert "do a thing" in captured["messages"][1]["content"]
    # generation params forwarded from BrainConfig
    assert captured["kw"]["max_tokens"] == 256
    assert captured["kw"]["temperature"] == 0.5


async def test_query_ai_none_when_ai_disabled(monkeypatch):
    b = _brain()
    b._ai_client = None
    assert await b._query_ai("x", "y") is None


async def test_query_ai_none_on_llm_error(monkeypatch):
    def boom(messages, **kw):
        raise RuntimeError("no provider configured")

    monkeypatch.setattr(gen_mod, "run_llm", boom)
    assert await _brain()._query_ai("x", "y") is None


async def test_query_ai_none_on_empty_content(monkeypatch):
    monkeypatch.setattr(gen_mod, "run_llm", lambda messages, **kw: _Result("   "))
    assert await _brain()._query_ai("x", "y") is None  # empty → None → fallback


def test_real_seam_is_present():
    from navig.llm.generate import run_llm

    assert callable(run_llm)
