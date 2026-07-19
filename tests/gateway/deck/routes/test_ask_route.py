"""
/api/deck/ask now dispatches through run_llm (connection-aware) and surfaces a
connection-aware rotation in its JSON so the deck can show "answered with
<model>" instead of a silent swap.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import ask as ask_mod

    app = web.Application()
    app.router.add_post("/ask", ask_mod.handle_deck_ask)
    return app


def _result(**kw):
    from navig.llm.types import LLMResult

    return LLMResult(**kw)


async def test_ask_surfaces_fallback(monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    def fake_run_llm(messages, **kw):
        return _result(
            content="hi there",
            model="claude-opus-4-8",
            provider="anthropic",
            raw={"fallback": {
                "from": "Claude A", "to": "Claude B",
                "reason": "rate_limited", "connection_id": "B",
            }},
        )

    monkeypatch.setattr("navig.llm.generate.run_llm", fake_run_llm)

    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/ask", json={"query": "hello"})
        assert r.status == 200
        body = await r.json()
        assert body["ok"] is True
        assert body["reply"] == "hi there"
        assert body["model"] == "claude-opus-4-8"
        assert body["provider"] == "anthropic"
        assert body["fallback"]["to"] == "Claude B"
        assert body["fallback"]["reason"] == "rate_limited"        # raw category preserved
        assert body["fallback"]["reason_label"] == "rate-limited"  # ready-to-render phrase


async def test_ask_no_fallback_omits_field(monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setattr(
        "navig.llm.generate.run_llm",
        lambda messages, **kw: _result(content="ok", model="m", provider="p"),
    )
    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/ask", json={"query": "hi"})
        body = await r.json()
        assert body["ok"] is True
        assert "fallback" not in body  # nothing rotated → no notice


async def test_ask_no_provider_returns_503(monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setattr(
        "navig.llm.generate.run_llm",
        lambda messages, **kw: _result(
            content="", model="m", provider="", finish_reason="error:ValueError: no key"
        ),
    )
    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/ask", json={"query": "hi"})
        assert r.status == 503
        body = await r.json()
        assert body["ok"] is False
        assert "provider" in body["error"].lower()


async def test_ask_empty_query_400(monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/ask", json={"query": "   "})
        assert r.status == 400
        body = await r.json()
        assert body["ok"] is False
