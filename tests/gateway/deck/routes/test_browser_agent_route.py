"""
POST /api/deck/agent/browser/turn — the NAVIG Dock side panel's streamed turn.

Frames arrive in order (route → delta… → actions → done), a failure is an honest
`error` frame, and a bad body is refused before any model call.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


def _app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import browser_agent as mod

    app = web.Application()
    app.router.add_post("/turn", mod.handle_deck_browser_agent_turn)
    return app


def _frames(raw: str) -> list[dict]:
    return [json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:")]


def _fake_stream(deltas, *, exc=None, calls=None):
    async def fake(*, messages, model_spec=None, connection_id=None, on_account=None, **kw):
        if calls is not None:
            calls.append({"messages": messages, "model_spec": model_spec, "connection_id": connection_id})
        if on_account:
            on_account({"name": "Claude", "connection_id": "c1", "provider": "anthropic", "model": "m"})
        for d in deltas:
            yield d
        if exc is not None:
            raise exc

    return fake


BODY = {
    "messages": [{"role": "user", "content": "log me in"}],
    "observation": {"url": "https://ex.test", "title": "Ex", "tree": 'button "Log in" [ref_3]'},
    "connection_id": "c1",
}


async def test_streams_route_deltas_actions_done(monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    calls: list = []
    monkeypatch.setattr(
        "navig.providers.inference.astream_via_connection",
        _fake_stream(["Clicking ", "Log in.\n```actions\n", '[{"action":"click","ref":"ref_3"}]\n```'],
                     calls=calls),
    )
    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/turn", json=BODY)
        assert r.status == 200
        assert r.headers["Content-Type"].startswith("text/event-stream")
        frames = _frames(await r.text())

    types = [f["type"] for f in frames]
    assert types[0] == "route" and frames[0]["name"] == "Claude"
    assert types[1:4] == ["delta", "delta", "delta"]
    assert frames[-2] == {"type": "actions", "actions": [{"action": "click", "ref": "ref_3", "clicks": 1}], "errors": []}
    assert frames[-1] == {"type": "done", "text": "Clicking Log in."}
    # the observation reached the model, the pinned connection was honoured
    assert calls[0]["connection_id"] == "c1"
    assert '[ref_3]' in calls[0]["messages"][-1]["content"]
    assert calls[0]["messages"][0]["role"] == "system"


async def test_plain_answer_has_no_actions_frame(monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setattr("navig.providers.inference.astream_via_connection", _fake_stream(["It is a login page."]))
    async with TestClient(TestServer(_app())) as client:
        frames = _frames(await (await client.post("/turn", json=BODY)).text())
    assert "actions" not in [f["type"] for f in frames]
    assert frames[-1] == {"type": "done", "text": "It is a login page."}


async def test_provider_failure_is_an_error_frame(monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    class RateLimited(Exception):
        status_code = 429

    monkeypatch.setattr(
        "navig.providers.inference.astream_via_connection",
        _fake_stream([], exc=RateLimited("429 Too Many Requests")),
    )
    async with TestClient(TestServer(_app())) as client:
        frames = _frames(await (await client.post("/turn", json=BODY)).text())
    assert frames[-1]["type"] == "error"
    assert frames[-1]["category"] == "rate_limited"
    assert "done" not in [f["type"] for f in frames]


@pytest.mark.parametrize(
    "body",
    [
        {"messages": []},
        {"messages": [{"role": "assistant", "content": "x"}]},
        ["not", "an", "object"],
    ],
)
async def test_bad_body_is_refused_before_any_model_call(monkeypatch, body):
    from aiohttp.test_utils import TestClient, TestServer

    calls: list = []
    monkeypatch.setattr("navig.providers.inference.astream_via_connection", _fake_stream(["x"], calls=calls))
    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/turn", json=body)
        assert r.status == 400
    assert calls == []


async def test_registered_on_the_deck_app():
    pytest.importorskip("aiohttp")
    import inspect

    import navig.gateway.deck as deck

    src = inspect.getsource(deck)
    assert '"/api/deck/agent/browser/turn", handle_deck_browser_agent_turn' in src


async def test_model_of_a_pinned_connection_keeps_its_colons(monkeypatch):
    # An ollama id like "llama3:8b" must reach resolve_route whole, not as provider "llama3".
    from aiohttp.test_utils import TestClient, TestServer

    from navig.providers.inference import _parse_spec

    calls: list = []
    monkeypatch.setattr("navig.providers.inference.astream_via_connection", _fake_stream(["ok"], calls=calls))
    async with TestClient(TestServer(_app())) as client:
        await (await client.post("/turn", json={**BODY, "connection_id": "c1", "model": "llama3:8b"})).text()
        await (await client.post("/turn", json={**BODY, "connection_id": None, "model": "openai:gpt-4o"})).text()
    assert _parse_spec(calls[0]["model_spec"]) == (None, "llama3:8b")
    assert calls[0]["connection_id"] == "c1"
    assert _parse_spec(calls[1]["model_spec"]) == ("openai", "gpt-4o")
