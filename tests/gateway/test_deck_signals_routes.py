"""The deck/OS management surface for Signal sources:
POST /api/deck/notify/signals {action: add|update|remove|rotate|enable|test}.

NAVIG OS's Settings → Signals page drives everything through these actions, so
each one is exercised the way the page calls it."""

from __future__ import annotations

import pytest

WHSEC = "whsec_deck_route_0123456789abcdefghij"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    from navig.notify import signals

    return signals


def _app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import notify

    app = web.Application()
    app.router.add_get("/api/deck/notify/signals", notify.handle_notify_signals_get)
    app.router.add_post("/api/deck/notify/signals", notify.handle_notify_signals_post)
    return app


async def _post(client, **body):
    resp = await client.post("/api/deck/notify/signals", json=body)
    return resp.status, await resp.json()


async def test_full_lifecycle_for_a_stripe_source(env, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    dispatched: list[tuple] = []

    async def _dispatch(nt, title, body, **kw):
        dispatched.append((nt, title, kw.get("data", {})))
        return {"channels": [{"channel": "deck", "ok": True}]}

    monkeypatch.setattr("navig.notify.dispatch", _dispatch)

    async with TestClient(TestServer(_app())) as client:
        status, out = await _post(client, action="add", name="grabnite", provider="stripe", secret=WHSEC)
        assert status == 200
        created = out["data"]["created"]
        assert created["provider"] == "stripe" and created["notify_type"] == "sale"
        assert created["ingest_url"].endswith("/grabnite")

        resp = await client.get("/api/deck/notify/signals")
        data = (await resp.json())["data"]
        assert [s["name"] for s in data["sources"]] == ["grabnite"]
        assert data["sources"][0]["secret"] != WHSEC  # masked in listings
        # The address can be shown before a source exists (Stripe needs it first).
        assert data["ingest_url_template"].endswith("/{name}")
        assert data["ingest_url_template"].replace("{name}", "grabnite") == data["sources"][0]["ingest_url"]
        stripe = next(p for p in data["providers"] if p["key"] == "stripe")
        assert "checkout.session.completed" in stripe["events"]

        status, out = await _post(client, action="update", name="grabnite", priority="high")
        assert status == 200 and out["data"]["updated"]["priority"] == "high"

        status, out = await _post(client, action="test", name="grabnite")
        assert status == 200
        assert dispatched[-1][0] == "sale" and dispatched[-1][2].get("_test") is True

        status, out = await _post(client, action="rotate", name="grabnite")
        assert status == 400 and "whsec_" in out["error"]
        new = "whsec_rolled_0123456789abcdefghijk"
        status, out = await _post(client, action="rotate", name="grabnite", secret=new)
        assert status == 200
        assert env.get_source("grabnite")["secret"] == new

        status, out = await _post(client, action="enable", name="grabnite", enabled=False)
        assert status == 200 and env.get_source("grabnite")["enabled"] is False

        status, out = await _post(client, action="remove", name="grabnite")
        assert status == 200 and out["data"]["removed"] is True
        assert env.list_sources() == []


async def test_update_only_touches_fields_that_were_sent(env):
    from aiohttp.test_utils import TestClient, TestServer

    env.add_source("shop", preset="payment_success")
    before = env.get_source("shop")
    async with TestClient(TestServer(_app())) as client:
        status, out = await _post(client, action="update", name="shop", body_tmpl="")
    assert status == 200
    after = out["data"]["updated"]
    assert after["body_tmpl"] is None  # sent as "" → cleared
    assert after["title_tmpl"] == before["title_tmpl"]  # not sent → untouched
    assert after["priority"] == before["priority"]


async def test_bad_input_is_a_400_with_the_reason(env):
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app())) as client:
        status, out = await _post(client, action="update", name="ghost", priority="low")
        assert status == 400 and "unknown signal source" in out["error"]
        status, out = await _post(client, action="add", name="x!", provider="stripe", secret=WHSEC)
        assert status == 400
        status, out = await _post(client, action="explode", name="x")
        assert status == 400 and "update" in out["error"]
