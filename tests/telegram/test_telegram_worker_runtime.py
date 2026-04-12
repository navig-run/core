"""Runtime tests for telegram_worker startup/shutdown paths."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.integration


class _PreSetEvent:
    def is_set(self) -> bool:
        return True

    def set(self) -> None:  # pragma: no cover - interface parity
        return None


async def test_run_requires_token(monkeypatch, navig_log_capture):
    from navig.daemon import telegram_worker as tw

    monkeypatch.setattr(tw, "_load_env", lambda: None)
    monkeypatch.setattr(tw, "_telegram_config", lambda: {"bot_token": ""})
    monkeypatch.setattr(tw.asyncio, "Event", _PreSetEvent)
    fake_loop = SimpleNamespace(add_signal_handler=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tw.asyncio, "get_running_loop", lambda: fake_loop)
    monkeypatch.setattr(tw, "_start_gateway_http", AsyncMock())
    monkeypatch.setattr(tw, "_stop_gateway_http", AsyncMock())

    await tw._run(enable_gateway=False)
    combined = "\n".join(navig_log_capture)
    assert "TELEGRAM_BOT_TOKEN not configured; Telegram bot will be disabled" in combined


async def test_run_hard_fails_on_invalid_messaging_provider(monkeypatch):
    from navig.daemon import telegram_worker as tw

    monkeypatch.setattr(tw, "_load_env", lambda: None)
    monkeypatch.setattr(tw, "get_config_manager", lambda: SimpleNamespace(global_config={}))
    monkeypatch.setattr(tw, "get_active_provider_name", lambda _cfg: "invalid-provider")

    with pytest.raises(SystemExit) as exc:
        await tw._run(enable_gateway=False)

    assert exc.value.code == 1


async def test_run_fails_when_channel_init_returns_none(monkeypatch, navig_log_capture):
    from navig.daemon import telegram_worker as tw

    gateway = SimpleNamespace(
        config=SimpleNamespace(port=8789), channels={}, mcp_client_manager=None
    )

    fake_loop = SimpleNamespace(add_signal_handler=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tw.asyncio, "Event", _PreSetEvent)
    monkeypatch.setattr(tw.asyncio, "get_running_loop", lambda: fake_loop)

    monkeypatch.setattr(tw, "_load_env", lambda: None)
    monkeypatch.setattr(
        tw,
        "_telegram_config",
        lambda: {
            "bot_token": "token",
            "allowed_users": [],
            "allowed_groups": [],
            "require_auth": True,
        },
    )
    monkeypatch.setattr(tw, "_deck_config", lambda: {"enabled": False})
    monkeypatch.setattr(tw, "NavigGateway", lambda: gateway)
    monkeypatch.setattr(tw, "create_telegram_channel", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tw, "_start_gateway_http", AsyncMock())
    monkeypatch.setattr(tw, "_stop_gateway_http", AsyncMock())

    await tw._run(enable_gateway=False)
    combined = "\n".join(navig_log_capture)
    assert "Failed to initialize Telegram channel" in combined


async def test_run_starts_and_stops_gateway_and_channel(monkeypatch):
    from navig.daemon import telegram_worker as tw

    fake_loop = SimpleNamespace(add_signal_handler=lambda *_args, **_kwargs: None)
    gateway = SimpleNamespace(config=SimpleNamespace(port=8789), channels={})
    channel = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    start_http = AsyncMock()
    stop_http = AsyncMock()

    monkeypatch.setattr(tw, "_load_env", lambda: None)
    monkeypatch.setattr(
        tw,
        "_telegram_config",
        lambda: {
            "bot_token": "token",
            "allowed_users": [],
            "allowed_groups": [],
            "require_auth": True,
        },
    )
    monkeypatch.setattr(tw, "_deck_config", lambda: {"enabled": True})
    monkeypatch.setattr(tw, "NavigGateway", lambda: gateway)
    monkeypatch.setattr(tw, "create_telegram_channel", lambda *_args, **_kwargs: channel)
    monkeypatch.setattr(tw, "_start_gateway_http", start_http)
    monkeypatch.setattr(tw, "_stop_gateway_http", stop_http)
    monkeypatch.setattr(tw.asyncio, "Event", _PreSetEvent)
    monkeypatch.setattr(tw.asyncio, "get_running_loop", lambda: fake_loop)

    await tw._run(enable_gateway=True)

    channel.start.assert_awaited_once()
    channel.stop.assert_awaited_once()
    start_http.assert_awaited_once()
    stop_http.assert_awaited_once()


async def test_start_gateway_http_registers_full_routes(monkeypatch):
    from aiohttp import web

    from navig.daemon import telegram_worker as tw

    gateway = SimpleNamespace(
        running=False,
        _runner=None,
        _app=None,
        _cors_middleware=lambda request, handler: handler(request),
        config=SimpleNamespace(host="127.0.0.1", port=9876),
        config_manager=SimpleNamespace(global_config={}),
    )

    register_all_routes = MagicMock()
    monkeypatch.setattr("navig.gateway.routes.register_all_routes", register_all_routes)
    monkeypatch.setattr("navig.gateway.deck.register_deck_routes", MagicMock())

    await tw._start_gateway_http(
        gateway,
        tg_config={"bot_token": "token", "allowed_users": [], "require_auth": True},
        deck_cfg={"enabled": False},
    )

    assert gateway.running is True
    assert isinstance(gateway._app, web.Application)
    register_all_routes.assert_called_once()

    await tw._stop_gateway_http(gateway)
