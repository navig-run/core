"""'What NAVIG knows about you' — approved facts only, cached until they change."""

from __future__ import annotations

import asyncio

import pytest

from navig.agent.memory_summary import generate_summary, read_summary
from navig.memory.key_facts import KeyFact, KeyFactStore


class _LLM:
    def __init__(self, reply: str = "- You prefer dark themes."):
        self.reply = reply
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def _store(tmp_path) -> KeyFactStore:
    return KeyFactStore(db_path=tmp_path / "kf.db")


def test_uses_approved_facts_only(tmp_path):
    s = _store(tmp_path)
    s.upsert(KeyFact(content="Prefers dark themes"))
    s.upsert(KeyFact(content="Maybe likes jazz", approved=None))
    llm = _LLM()
    out = asyncio.run(generate_summary(s, llm_call=llm))
    assert out["summary"] == "- You prefer dark themes." and out["fact_count"] == 1
    assert "dark themes" in llm.prompts[0] and "jazz" not in llm.prompts[0]


def test_cached_until_the_facts_change(tmp_path):
    s = _store(tmp_path)
    s.upsert(KeyFact(content="Prefers dark themes"))
    llm = _LLM()
    asyncio.run(generate_summary(s, llm_call=llm))
    asyncio.run(generate_summary(s, llm_call=llm))
    assert len(llm.prompts) == 1
    assert read_summary(s)["stale"] is False
    s.upsert(KeyFact(content="Lives in Lisbon"))
    assert read_summary(s)["stale"] is True
    asyncio.run(generate_summary(s, llm_call=llm))
    assert len(llm.prompts) == 2


def test_force_regenerates(tmp_path):
    s = _store(tmp_path)
    s.upsert(KeyFact(content="Prefers dark themes"))
    llm = _LLM()
    asyncio.run(generate_summary(s, llm_call=llm))
    asyncio.run(generate_summary(s, force=True, llm_call=llm))
    assert len(llm.prompts) == 2


def test_no_facts_means_no_llm_call(tmp_path):
    llm = _LLM()
    out = asyncio.run(generate_summary(_store(tmp_path), llm_call=llm))
    assert out["summary"] == "" and llm.prompts == []


def test_empty_model_reply_raises_and_keeps_no_fake_summary(tmp_path):
    s = _store(tmp_path)
    s.upsert(KeyFact(content="Prefers dark themes"))
    with pytest.raises(RuntimeError):
        asyncio.run(generate_summary(s, llm_call=_LLM(reply="  ")))
    assert read_summary(s)["summary"] == ""
