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
