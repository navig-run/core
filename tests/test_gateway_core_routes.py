"""Integration tests for gateway core/deck route behavior."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_core_websocket_flow():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gateway = _build_gateway()
    app = _build_core_app(gateway)

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/ws")

        await ws.send_json({"action": "ping"})
        pong = await ws.receive_json()
        assert pong["action"] == "pong"

        await ws.send_json({"action": "subscribe", "topic": "events.system"})
        subscribed = await ws.receive_json()
        assert subscribed["action"] == "subscribed"
        assert subscribed["topic"] == "events.system"
        assert "events.system" in subscribed["subscriptions"]

        await ws.send_json({"action": "message", "message": "status"})
        routed = await ws.receive_json()
        assert routed["action"] == "response"
        assert routed["ok"] is True
        assert routed["data"]["response"] == "ok"

        await ws.send_json({"action": "unsupported"})
        unsupported = await ws.receive_json()
        assert unsupported["error_code"] == "unsupported_action"

        await ws.close()


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_deck_auth_middleware_allows_dev_header_user():
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from navig.gateway.deck_api import register_deck_routes

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


@pytest.mark.asyncio
async def test_deck_auth_middleware_forbidden_user():
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from navig.gateway.deck_api import register_deck_routes

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
