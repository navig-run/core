"""Integration tests for the signed Signals ingest route (POST /api/ingest/{source})
end-to-end through aiohttp: good/bad/missing signature, unknown source, replay,
and that a verified event lands in the deck feed."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    from navig.notify import feed, signals

    return signals, feed


def _build_app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.routes import ingest

    app = web.Application()
    ingest.register(app, None)  # the route doesn't need the gateway handle
    return app


def _headers(secret: str, body: bytes, ts: str | None = None) -> dict[str, str]:
    ts = ts or str(int(time.time()))
    sig = "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Navig-Timestamp": ts, "X-Navig-Signature": sig, "Content-Type": "application/json"}


async def test_signed_event_reaches_deck_feed(env):
    signals, feed = env
    secret = signals.add_source("hookdemo")["secret"]

    from aiohttp.test_utils import TestClient, TestServer

    body = json.dumps({"event": "deploy", "status": "green"}).encode()
    async with TestClient(TestServer(_build_app())) as client:
        resp = await client.post("/api/ingest/hookdemo", data=body, headers=_headers(secret, body))
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert "deck" in data["delivered"]

    # The deck channel wrote a feed item, routed to the source's own row.
    items = feed.list_items()
    assert len(items) == 1
    assert items[0]["type"] == "signal:hookdemo"
    assert items[0]["data"]["source"] == "hookdemo"


async def test_bad_signature_rejected(env):
    signals, _feed = env
    signals.add_source("h2")
    from aiohttp.test_utils import TestClient, TestServer

    body = b'{"x":1}'
    headers = _headers("sk_sig_wrongsecret", body)
    async with TestClient(TestServer(_build_app())) as client:
        resp = await client.post("/api/ingest/h2", data=body, headers=headers)
        assert resp.status == 401


async def test_unknown_source_404(env):
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_build_app())) as client:
        resp = await client.post("/api/ingest/ghost", data=b"{}", headers=_headers("x", b"{}"))
        assert resp.status == 404


async def test_missing_signature_401(env):
    signals, _feed = env
    signals.add_source("h3")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_build_app())) as client:
        resp = await client.post("/api/ingest/h3", data=b"{}", headers={"Content-Type": "application/json"})
        assert resp.status == 401


async def test_replay_is_deduped(env):
    signals, feed = env
    secret = signals.add_source("h4")["secret"]
    from aiohttp.test_utils import TestClient, TestServer

    body = json.dumps({"n": 1}).encode()
    headers = _headers(secret, body, ts=str(int(time.time())))
    async with TestClient(TestServer(_build_app())) as client:
        first = await client.post("/api/ingest/h4", data=body, headers=headers)
        first_body = await first.json()
        second = await client.post("/api/ingest/h4", data=body, headers=headers)
        second_body = await second.json()
    assert first.status == 200 and first_body.get("duplicate") is not True
    assert second_body.get("duplicate") is True
    # Only the first dispatched to the feed.
    assert len(feed.list_items()) == 1
