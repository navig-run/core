from __future__ import annotations

from navig.agent.conversational_legacy import ConversationalAgent
import pytest

pytestmark = pytest.mark.integration


def test_build_kb_context_uses_zero_arg_wiki_rag(monkeypatch):
    class _FakeStore:
        def search(self, _query, limit=10):
            return []

    class _FakeRag:
        def search(self, _query, top_k=3):
            return [{"title": "Deploy Guide", "chunk": "Use blue/green rollout."}]

    calls = {"count": 0}

    def _fake_get_wiki_rag():
        calls["count"] += 1
        return _FakeRag()

    monkeypatch.setattr(
        "navig.agent.conversational_legacy.get_key_fact_store",
        lambda: _FakeStore(),
    )
    monkeypatch.setattr("navig.wiki_rag.get_wiki_rag", _fake_get_wiki_rag)

    agent = ConversationalAgent(soul_content="test soul")
    block = agent._build_kb_context("how should I deploy?")

    assert calls["count"] == 1
    assert "<wiki>" in block
    assert "Deploy Guide" in block
