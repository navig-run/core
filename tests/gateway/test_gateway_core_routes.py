"""Integration tests for gateway core/deck route behavior."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.integration


def _build_gateway(*, auth_token: str | None = None):
    now = datetime.now()
    session = SimpleNamespace(messages=["hi"], created_at=now, updated_at=now)

    gateway = MagicMock()
    gateway.start_time = now - timedelta(seconds=30)
    gateway.running = True
    gateway.config = SimpleNamespace(
        port=8789,
        host="127.0.0.1",
        heartbeat_enabled=True,
        heartbeat_interval="30m",
        auth_token=auth_token,
    )
    gateway.sessions = SimpleNamespace(sessions={"telegram:1": session})
    gateway.heartbeat_runner = None
    gateway.cron_service = None
    gateway.router = MagicMock()
    gateway.router.route_message = AsyncMock(return_value="ok")
    gateway.system_events = MagicMock()
    gateway.system_events.enqueue = AsyncMock()
    gateway.stop = AsyncMock()
    return gateway


def _build_core_app(gateway):
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.routes.core import register

    app = web.Application()
    register(app, gateway)
    return app


async def test_core_health_status_and_message():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gateway = _build_gateway()
    app = _build_core_app(gateway)

    async with TestClient(TestServer(app)) as client:
        health = await client.get("/health")
        assert health.status == 200
        health_body = await health.json()
        assert health_body["ok"] is True
        assert health_body["data"]["status"] == "ok"

        status = await client.get("/status")
        assert status.status == 200
        status_body = await status.json()
        assert status_body["ok"] is True
        assert status_body["data"]["status"] == "running"
        assert status_body["data"]["config"]["port"] == 8789

        message = await client.post(
            "/message",
            json={"channel": "http", "user_id": "u1", "message": "hello"},
        )
        assert message.status == 200
        message_body = await message.json()
        assert message_body["ok"] is True
        assert message_body["data"]["response"] == "ok"


async def test_core_websocket_flow():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gateway = _build_gateway()
    app = _build_core_app(gateway)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/ws")

        assert hasattr(gateway, "ws_connections")
        assert len(gateway.ws_connections) == 1

        await ws.send_json({"action": "ping"})
        msg = await ws.receive()
        pong = json.loads(msg.data)
        assert pong["action"] == "pong"

        await ws.send_json({"action": "subscribe", "topic": "events.system"})
        msg = await ws.receive()
        subscribed = json.loads(msg.data)
        assert subscribed["action"] == "subscribed"
        assert subscribed["topic"] == "events.system"
        assert "events.system" in subscribed["subscriptions"]

        await ws.send_json({"action": "message", "message": "status"})
        msg = await ws.receive()
        routed = json.loads(msg.data)
        assert routed["action"] == "response"
        assert routed["ok"] is True
        assert routed["data"]["response"] == "ok"

        await ws.send_json({"action": "unsupported"})
        msg = await ws.receive()
        unsupported = json.loads(msg.data)
        assert unsupported["error_code"] == "unsupported_action"

        await ws.close()


@pytest.mark.timeout(60)
async def test_ws_topic_filtering_end_to_end():
    """Subscriptions on /ws must be honored by the broadcast path.

    - subscribed to a matching topic (exact or glob) → receives the broadcast
    - subscribed to a non-matching topic → does NOT receive it
    - never subscribed → receives everything (backward compatible)

    Regression for the write-only `_ws_subscriptions` map (ws-smoke-report
    known issue 1): on the old code every client received every broadcast.
    """
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.channel_router import ChannelRouter

    gateway = _build_gateway()
    app = _build_core_app(gateway)

    async with TestClient(TestServer(app)) as client:
        ws_match = await client.ws_connect("/ws")  # subscribes to "status"
        ws_glob = await client.ws_connect("/ws")  # subscribes to "stat*"
        ws_other = await client.ws_connect("/ws")  # subscribes to "other.topic"
        ws_legacy = await client.ws_connect("/ws")  # never subscribes

        for sock, topic in ((ws_match, "status"), (ws_glob, "stat*"), (ws_other, "other.topic")):
            await sock.send_json({"action": "subscribe", "topic": topic})
            ack = json.loads((await sock.receive()).data)
            assert ack["action"] == "subscribed"

        # Drive the real broadcast site (channel_router._broadcast_status).
        router = ChannelRouter(gateway)
        await router._broadcast_status("telegram:1", "thinking…")

        for sock in (ws_match, ws_glob, ws_legacy):
            msg = await sock.receive(timeout=2)
            body = json.loads(msg.data)
            assert body["type"] == "status"
            assert body["session"] == "telegram:1"
            assert body["message"] == "thinking…"

        # The non-matching subscriber must NOT have received the status.
        # Marker trick: the next frame it sees after an app-level ping must be
        # the pong — if the status had been delivered it would arrive first.
        await ws_other.send_json({"action": "ping"})
        first = json.loads((await ws_other.receive(timeout=2)).data)
        assert first["action"] == "pong", f"filtered client received broadcast: {first}"

        for sock in (ws_match, ws_glob, ws_other, ws_legacy):
            await sock.close()


async def test_ws_broadcast_prunes_dead_connection():
    """broadcast_ws is best-effort: a failing socket never blocks the rest and
    is pruned (connection + subscription entry) so it stops consuming pushes."""

    from navig.gateway.ws_broadcast import broadcast_ws

    class _DeadWS:
        async def send_json(self, payload):
            raise ConnectionResetError("gone")

    class _LiveWS:
        def __init__(self):
            self.sent = []

        async def send_json(self, payload):
            self.sent.append(payload)

    gw = SimpleNamespace()
    dead, live = _DeadWS(), _LiveWS()
    gw.ws_connections = {dead, live}
    gw._ws_subscriptions = {id(dead): set(), id(live): set()}

    delivered = await broadcast_ws(gw, {"type": "status"}, topic="status")

    assert delivered == 1
    assert live.sent == [{"type": "status"}]
    assert dead not in gw.ws_connections
    assert id(dead) not in gw._ws_subscriptions
    assert id(live) in gw._ws_subscriptions


@pytest.mark.timeout(60)
async def test_ws_server_heartbeat_reaps_dead_client(monkeypatch):
    """The server must ping and close a client that never answers.

    Regression for the missing server-side heartbeat (ws-smoke-report known
    issue 3): on the old code the server never pinged, so this test's receive
    loop would see neither a PING nor a close and time out.
    """
    pytest.importorskip("aiohttp")
    import asyncio

    import aiohttp
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setattr("navig.gateway.routes.core.WS_HEARTBEAT_SECONDS", 0.1)

    gateway = _build_gateway()
    app = _build_core_app(gateway)

    async with TestClient(TestServer(app)) as client:
        # autoping=False: this client never answers protocol PINGs — a zombie.
        ws = await client.ws_connect("/ws", autoping=False)
        assert len(gateway.ws_connections) == 1

        saw_ping = False
        closed = False
        for _ in range(20):
            msg = await ws.receive(timeout=5)
            if msg.type == aiohttp.WSMsgType.PING:
                saw_ping = True
                continue
            if msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                closed = True
                break
        assert saw_ping, "server never sent a protocol PING"
        assert closed, "server never closed the unresponsive connection"

        # The handler's finally block must unregister the reaped connection.
        for _ in range(50):
            if len(gateway.ws_connections) == 0:
                break
            await asyncio.sleep(0.05)
        assert len(gateway.ws_connections) == 0


@pytest.mark.timeout(60)
async def test_ws_heartbeat_transparent_to_normal_clients(monkeypatch):
    """A listening client that answers protocol PINGs (every real WS stack
    does automatically while reading — browsers and Node `ws` even without
    reading) but never sends an app-level pong must survive multiple heartbeat
    intervals — the heartbeat must not break existing clients."""
    pytest.importorskip("aiohttp")
    import asyncio

    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setattr("navig.gateway.routes.core.WS_HEARTBEAT_SECONDS", 0.1)

    gateway = _build_gateway()
    app = _build_core_app(gateway)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/ws")  # autoping=True (default)

        # Sit in a receive loop for ~5 heartbeat intervals, like every real
        # consumer of /ws does (waiting for events). autoping answers the
        # server's protocol PINGs during the pending receive; nothing app-level
        # is ever sent. NB: bound the wait with an *external* wait_for —
        # `ws.receive(timeout=…)` re-arms its timer on every internal frame,
        # so heartbeat PINGs arriving faster than the timeout would make it
        # wait forever.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ws.receive(), timeout=0.5)

        await ws.send_json({"action": "ping"})
        pong = json.loads((await ws.receive(timeout=2)).data)
        assert pong["action"] == "pong"
        assert len(gateway.ws_connections) == 1

        await ws.close()


async def test_core_auth_enforced_when_token_is_set():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gateway = _build_gateway(auth_token="top-secret")
    app = _build_core_app(gateway)

    async with TestClient(TestServer(app)) as client:
        health = await client.get("/health")
        assert health.status == 200

        unauthorized = await client.get("/status")
        assert unauthorized.status == 401
        unauthorized_body = await unauthorized.json()
        assert unauthorized_body["error_code"] == "unauthorized"

        authorized = await client.get("/status", headers={"Authorization": "Bearer top-secret"})
        assert authorized.status == 200


async def test_deck_auth_middleware_allows_dev_header_user():
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck import register_deck_routes

    app = web.Application()
    register_deck_routes(
        app,
        bot_token="",
        allowed_users=[123],
        require_auth=True,
        deck_cfg={"dev_mode": True},
    )

    async with TestClient(TestServer(app)) as client:
        denied = await client.get("/api/deck/status")
        assert denied.status == 401

        allowed = await client.get("/api/deck/status", headers={"X-Telegram-User": "123"})
        assert allowed.status == 200
        allowed_body = await allowed.json()
        assert "avatar_state" in allowed_body


async def test_deck_status_reports_event_processor_health(tmp_path):
    """/api/deck/status carries the additive `events` block when the gateway
    exposes its SystemEventQueue — the surface `navig doctor` asserts on."""
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck import register_deck_routes
    from navig.gateway.system_events import SystemEventQueue

    queue = SystemEventQueue(storage_path=tmp_path)
    gateway = MagicMock()
    gateway.task_queue = None
    gateway.system_events = queue

    app = web.Application()
    app["gateway"] = gateway
    register_deck_routes(
        app,
        bot_token="",
        allowed_users=[123],
        require_auth=True,
        deck_cfg={"dev_mode": True},
    )

    async with TestClient(TestServer(app)) as client:
        headers = {"X-Telegram-User": "123"}

        # Processor not started: running=False must be visible (the failure class).
        await queue.emit("board_update", {"kind": "test"})
        resp = await client.get("/api/deck/status", headers=headers)
        assert resp.status == 200
        body = await resp.json()
        assert body["events"] == {"running": False, "pending": 1, "history": 0}
        assert "avatar_state" in body  # existing fields untouched — additive only

        try:
            await queue.start(replay_pending=False)
            resp = await client.get("/api/deck/status", headers=headers)
            body = await resp.json()
            assert body["events"]["running"] is True
            assert body["events"]["pending"] == 0
            assert body["events"]["history"] == 1  # stale backlog kept as discarded
        finally:
            await queue.stop()


async def test_deck_status_omits_events_without_gateway():
    """No gateway on the app (older mounts, isolated tests) → the field is
    absent, so consumers can tell 'not exposed' apart from 'not running'."""
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck import register_deck_routes

    app = web.Application()
    register_deck_routes(
        app,
        bot_token="",
        allowed_users=[123],
        require_auth=True,
        deck_cfg={"dev_mode": True},
    )

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/deck/status", headers={"X-Telegram-User": "123"})
        assert resp.status == 200
        body = await resp.json()
        assert "events" not in body


async def test_deck_auth_middleware_forbidden_user():
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck import register_deck_routes

    app = web.Application()
    register_deck_routes(
        app,
        bot_token="",
        allowed_users=[999],
        require_auth=True,
        deck_cfg={"dev_mode": True},
    )

    async with TestClient(TestServer(app)) as client:
        forbidden = await client.get("/api/deck/status", headers={"X-Telegram-User": "123"})
        assert forbidden.status == 403
        body = await forbidden.json()
        assert body["error"] == "forbidden"
