"""Unit tests for the Lighthouse uplink client + CloudManager lighthouse mode.

These cover the pure logic (URL derivation, tenant hashing, frame dispatch,
ping/pong, event drain) without any real network — the WebSocket and loopback
HTTP are stubbed. asyncio_mode=auto, so `async def test_*` runs directly.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from navig.cloud import CloudManager, UplinkClient, api_key_hash


def _client(**kw) -> UplinkClient:
    base = dict(
        lighthouse_url="https://navig-lighthouse.example.workers.dev",
        api_key="navig_testkey",
        gateway_port=8765,
    )
    base.update(kw)
    return UplinkClient(**base)


# ── tenant + URL derivation ────────────────────────────────────────────────

def test_api_key_hash_is_sha256_hex():
    key = "navig_testkey"
    assert api_key_hash(key) == hashlib.sha256(key.encode()).hexdigest()
    assert len(api_key_hash(key)) == 64


def test_ws_url_upgrades_scheme():
    assert _client()._ws_url() == "wss://navig-lighthouse.example.workers.dev/uplink"
    c = _client(lighthouse_url="http://localhost:8787")
    assert c._ws_url() == "ws://localhost:8787/uplink"


def test_webhook_urls_use_opaque_tenant_path():
    c = _client()
    h = api_key_hash("navig_testkey")
    assert c.telegram_webhook_url() == f"https://navig-lighthouse.example.workers.dev/tg/{h}"
    assert c.sms_webhook_url() == f"https://navig-lighthouse.example.workers.dev/sms/{h}"


# ── frame dispatch ──────────────────────────────────────────────────────────

async def test_dispatch_telegram_invokes_handler_with_secret():
    handler = AsyncMock(return_value=True)
    c = _client(telegram_handler=handler)
    update = {"update_id": 1, "message": {"text": "hi"}}
    frame = {
        "id": "r1",
        "kind": "telegram",
        "method": "POST",
        "path": "/tg/abc",
        "headers": {"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"},
        "body": json.dumps(update),
    }
    status, headers, body = await c._dispatch_telegram(frame)
    handler.assert_awaited_once_with(update, "s3cr3t")
    assert status == 200
    assert json.loads(body) == {"ok": True}


async def test_dispatch_telegram_without_handler_returns_503():
    c = _client(telegram_handler=None)
    status, _, body = await c._dispatch_telegram({"body": "{}", "headers": {}})
    assert status == 503
    assert "telegram_off" in body


async def test_dispatch_sends_res_frame_and_counts():
    c = _client()
    c._dispatch_loopback = AsyncMock(  # type: ignore[method-assign]
        return_value=(201, {"content-type": "application/json"}, '{"ok":1}')
    )
    c._send = AsyncMock()  # type: ignore[method-assign]
    await c._dispatch(
        {"id": "abc", "kind": "deck", "method": "GET", "path": "/api/deck/status", "headers": {}, "body": ""}
    )
    c._dispatch_loopback.assert_awaited_once()
    sent = c._send.await_args.args[0]
    assert sent["t"] == "res"
    assert sent["id"] == "abc"
    assert sent["status"] == 201
    assert c.state.requests_served == 1


async def test_dispatch_failure_returns_502():
    c = _client()
    c._dispatch_loopback = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    c._send = AsyncMock()  # type: ignore[method-assign]
    await c._dispatch({"id": "x", "kind": "deck", "method": "GET", "path": "/api/x", "headers": {}, "body": ""})
    sent = c._send.await_args.args[0]
    assert sent["status"] == 502


async def test_ping_frame_replies_pong():
    import asyncio

    c = _client()
    c._send = AsyncMock()  # type: ignore[method-assign]
    c._on_frame(json.dumps({"t": "ping"}))
    await asyncio.sleep(0)  # let the create_task fire
    c._send.assert_awaited_with({"t": "pong"})


async def test_event_drain_forwards_as_message_event():
    import asyncio

    c = _client()
    c._send = AsyncMock()  # type: ignore[method-assign]
    payload = json.dumps({"type": "notification", "data": {"x": 1}})
    c._event_q.put_nowait(payload)
    task = asyncio.create_task(c._event_drain_loop())
    await asyncio.sleep(0.01)
    task.cancel()
    sent = c._send.await_args.args[0]
    assert sent == {"t": "event", "event": "message", "data": payload}


# ── CloudManager mode detection ──────────────────────────────────────────────

def test_manager_mode_lighthouse_takes_precedence():
    m = CloudManager(
        api_key="k",
        broker_url="https://api.navig.run",
        gateway_port=8765,
        lighthouse_url="https://x.workers.dev",
        public_url="https://also.example.com",
    )
    assert m.mode == "lighthouse"


async def test_lighthouse_registers_stable_url_with_broker(monkeypatch):
    """Regression: lighthouse mode MUST publish its stable workers.dev URL to the
    broker and bind Telegram users.

    Without this the broker keeps a dead `*.trycloudflare.com` URL from a prior
    cloudflared session, the Mini App resolves to it, gets HTTP 530, and shows
    'Connection Lost' forever. The fix registers the lighthouse URL + binds users
    so resolve-by-telegram-id returns the always-up edge.
    """
    import navig.cloud.manager as mgr
    import navig.notify.producers.connectivity as conn_mod

    class _FakeUplink:
        status = "online"
        def __init__(self, **kw): self.kw = kw
        async def start(self): pass
        async def stop(self): pass

    class _FakeConnectivity:
        def __init__(self, **kw): pass
        async def on_status(self, *a, **k): pass

    registered: list[tuple[str, str | None]] = []
    bound: list[int] = []

    class _FakeBroker:
        def __init__(self, broker_url, api_key, timeout_s=15.0): pass
        async def register(self, url, label=None):
            registered.append((url, label)); return "uid-1"
        async def bind_telegram(self, uid): bound.append(int(uid)); return uid
        async def close(self): pass

    monkeypatch.setattr("navig.cloud.uplink.UplinkClient", _FakeUplink)
    monkeypatch.setattr(conn_mod, "ConnectivityReporter", _FakeConnectivity)
    monkeypatch.setattr(mgr, "BrokerClient", _FakeBroker)

    class _CM:
        global_config = {"telegram": {"allowed_users": [123, 456]}}
    monkeypatch.setattr("navig.config.get_config_manager", lambda: _CM())

    m = CloudManager(
        api_key="k",
        broker_url="https://api.navig.run",
        gateway_port=8765,
        lighthouse_url="https://navig-lighthouse.example.workers.dev",
    )
    await m.start()

    assert registered == [("https://navig-lighthouse.example.workers.dev", m.tunnel_label or None)]
    assert bound == [123, 456]
    assert m.state.status == "online"
    assert m.state.tunnel_url == "https://navig-lighthouse.example.workers.dev"


async def test_lighthouse_broker_failure_is_non_fatal(monkeypatch):
    """A broker hiccup during lighthouse register must NOT break the uplink — the
    uplink is the data path; broker is only a Mini App routing convenience."""
    import navig.cloud.manager as mgr
    import navig.notify.producers.connectivity as conn_mod

    class _FakeUplink:
        status = "online"
        def __init__(self, **kw): pass
        async def start(self): pass
        async def stop(self): pass

    class _FakeConnectivity:
        def __init__(self, **kw): pass
        async def on_status(self, *a, **k): pass

    class _BoomBroker:
        def __init__(self, *a, **k): pass
        async def register(self, *a, **k): raise RuntimeError("broker down")
        async def close(self): pass

    monkeypatch.setattr("navig.cloud.uplink.UplinkClient", _FakeUplink)
    monkeypatch.setattr(conn_mod, "ConnectivityReporter", _FakeConnectivity)
    monkeypatch.setattr(mgr, "BrokerClient", _BoomBroker)
    monkeypatch.setattr("navig.config.get_config_manager",
                        lambda: type("_CM", (), {"global_config": {}})())

    m = CloudManager(
        api_key="k", broker_url="https://api.navig.run", gateway_port=8765,
        lighthouse_url="https://navig-lighthouse.example.workers.dev",
    )
    await m.start()  # must not raise
    assert m.state.status == "online"


async def test_lighthouse_register_failure_does_not_set_last_error():
    """In lighthouse mode a broker register failure (e.g. a broker that predates
    *.workers.dev URLs → bad_tunnel_url) must NOT surface as last_error — the uplink
    is the data path, so an otherwise-healthy brain must keep a clean status."""
    from navig.cloud.broker_client import BrokerError

    m = CloudManager(
        api_key="k", broker_url="https://api.navig.run", gateway_port=8765,
        lighthouse_url="https://navig-lighthouse.example.workers.dev",
    )
    m.state.tunnel_url = "https://navig-lighthouse.example.workers.dev"

    class _BadTunnelBroker:
        async def register(self, *a, **k):
            raise BrokerError(400, "bad_tunnel_url")

    m._broker = _BadTunnelBroker()  # type: ignore[assignment]
    await m._register_current_url()
    assert m.state.last_error is None


async def test_non_lighthouse_register_failure_sets_last_error():
    """In tunnel/direct mode the broker IS the routing path, so a register failure
    is a genuine error and must still be surfaced."""
    from navig.cloud.broker_client import BrokerError

    m = CloudManager(api_key="k", broker_url="https://api.navig.run", gateway_port=8765)
    assert m.mode == "tunnel"
    m.state.tunnel_url = "https://x.trycloudflare.com"

    class _BadTunnelBroker:
        async def register(self, *a, **k):
            raise BrokerError(400, "bad_tunnel_url")

    m._broker = _BadTunnelBroker()  # type: ignore[assignment]
    await m._register_current_url()
    assert m.state.last_error and m.state.last_error.startswith("register:")


def test_manager_mode_direct_and_tunnel():
    assert (
        CloudManager(api_key="k", broker_url="b", gateway_port=1, public_url="https://h").mode
        == "direct"
    )
    assert CloudManager(api_key="k", broker_url="b", gateway_port=1).mode == "tunnel"


def test_manager_snapshot_merges_uplink_state():
    m = CloudManager(
        api_key="k", broker_url="b", gateway_port=1, lighthouse_url="https://x.workers.dev"
    )

    class _FakeUplink:
        status = "online"

        def snapshot(self):
            return {"status": "online", "requests_served": 3}

    m._uplink = _FakeUplink()
    snap = m.snapshot()
    assert snap["status"] == "online"
    assert snap["lighthouse"]["requests_served"] == 3
