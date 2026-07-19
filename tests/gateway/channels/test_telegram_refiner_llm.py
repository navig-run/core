"""Regression: the Telegram RefinementEngine actually reaches the LLM.

`_get_llm` imported `get_router` from `navig.agent.router` and `get_llm` from
`navig.agent` — neither has ever existed — so every refinement raised "cannot
get LLM": the refine flow showed "❌ LLM error during refinement" and question
generation silently fell back to canned questions. It now routes through the
canonical `navig.llm.run_llm` seam (the same one the deck `/ask` route uses).
"""

from __future__ import annotations

import navig.llm.generate as gen_mod
from navig.gateway.channels.telegram_refiner import RefinementEngine


class _Result:
    def __init__(self, content):
        self.content = content


def _engine():
    # _call_llm / _generate_questions don't touch the channel, so None is fine.
    return RefinementEngine(channel=None)


async def test_call_llm_routes_through_run_llm(monkeypatch):
    seen = {}

    def fake_run_llm(messages, **kw):
        seen["messages"] = messages
        return _Result("  refined text  ")

    monkeypatch.setattr(gen_mod, "run_llm", fake_run_llm)

    out = await _engine()._call_llm("please refine")

    assert out == "refined text"  # content, stripped
    assert seen["messages"] == [{"role": "user", "content": "please refine"}]


async def test_generate_questions_parses_llm_json(monkeypatch):
    monkeypatch.setattr(
        gen_mod,
        "run_llm",
        lambda messages, **kw: _Result('Questions: ["Q1?", "Q2?", "Q3?", "Q4?"]'),
    )

    qs = await _engine()._generate_questions("some text", "topic")

    assert qs == ["Q1?", "Q2?", "Q3?"]  # parsed and capped at 3


async def test_generate_questions_falls_back_on_llm_error(monkeypatch):
    def boom(messages, **kw):
        raise RuntimeError("no provider configured")

    monkeypatch.setattr(gen_mod, "run_llm", boom)

    qs = await _engine()._generate_questions("some text", "topic")

    assert len(qs) == 3
    assert all(isinstance(q, str) and q for q in qs)
    assert "primary goal or audience" in qs[0]  # the canned fallback set


def test_real_seam_exists_not_the_phantom():
    from navig.llm.generate import run_llm  # noqa: F401

    # the phantom _get_llm (which imported the never-existent get_router/get_llm)
    # is gone; the real dispatch seam is callable.
    assert not hasattr(RefinementEngine, "_get_llm")
    assert callable(run_llm)
