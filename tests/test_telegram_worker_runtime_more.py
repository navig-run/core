import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from navig.daemon import telegram_worker as tw


@pytest.fixture
def mock_config_manager(monkeypatch):
    cm = MagicMock()
    cm.global_config = {
        "telegram": {
            "bot_token": "tg_token",
            "allowed_users": [],
            "require_auth": False,
        },
        "deck": {"enabled": True, "port": 1234, "bind": "0.0.0.0", "dev_mode": True},
        "matrix": {"enabled": True, "homeserver": "https://matrix.org"},
        "bridge": {"mcp_auto_connect": True, "mcp_url": "ws://mcp"},
        "gateway": {"host": "127.0.0.1", "port": 8789},
    }
    monkeypatch.setattr(tw, "get_config_manager", lambda: cm)
    monkeypatch.setattr(tw, "resolve_telegram_bot_token", lambda _cfg=None: "tg_token")
    monkeypatch.setattr(tw, "resolve_telegram_uid", lambda _cfg=None: None)
    return cm


def test_load_env(monkeypatch):
    class MockPath:
        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def cwd(cls):
            return cls()

        def resolve(self):
            return self

        @property
        def parent(self):
            return self

        def exists(self):
            return True

        def __truediv__(self, other):
            return self

    monkeypatch.setattr(tw, "Path", MockPath)
    monkeypatch.setattr(tw, "NAVIG_HOME", MockPath())

    loaded = []

    def mock_load_dotenv(path):
        loaded.append(path)
    monkeypatch.setitem(sys.modules, "dotenv", SimpleNamespace(load_dotenv=mock_load_dotenv))

    tw._load_env()
    assert len(loaded) in (0, 1)


def test_configs(mock_config_manager, monkeypatch):
    monkeypatch.setattr(tw, "resolve_telegram_bot_token", lambda _cfg=None: "env_token")
    monkeypatch.setenv("NAVIG_BRIDGE_MCP_URL", "ws://env_mcp")
    monkeypatch.setenv("NAVIG_BRIDGE_LLM_TOKEN", "env_llm")

    tg_cfg = tw._telegram_config()
    assert tg_cfg["bot_token"] == "env_token"

    deck_cfg = tw._deck_config()
    assert deck_cfg["enabled"] is True
    assert deck_cfg["port"] == 1234

    matrix_cfg = tw._matrix_config()
    assert matrix_cfg["enabled"] is True
    assert matrix_cfg["homeserver"] == "https://matrix.org"

    mcp_cfg = tw._mcp_bridge_config()
    assert mcp_cfg["mcp_url"] == "ws://env_mcp"
    assert mcp_cfg["token"] == "env_llm"


@pytest.mark.asyncio
async def test_start_stop_gateway_http(monkeypatch):
    gateway = SimpleNamespace()
    gateway._cors_middleware = MagicMock()
    gateway.config = SimpleNamespace(host="127.0.0.1", port=8080)

    class DummyApp:
        def __init__(self, middlewares=None):
            self.middlewares = middlewares

    class DummyRunner:
        def __init__(self, app):
            self.app = app

        async def setup(self):
            pass

        async def cleanup(self):
            pass

    class DummySite:
        def __init__(self, runner, host, port):
            self.runner = runner

        async def start(self):
            pass

    import aiohttp.web as web

    monkeypatch.setattr(web, "Application", DummyApp)
    monkeypatch.setattr(web, "AppRunner", DummyRunner)
    monkeypatch.setattr(web, "TCPSite", DummySite)

    monkeypatch.setattr("navig.gateway.routes.register_all_routes", MagicMock())
    monkeypatch.setattr("navig.gateway.deck.register_deck_routes", MagicMock())

    await tw._start_gateway_http(gateway, {}, {"enabled": True})
    assert gateway.running is True
    assert isinstance(gateway._runner, DummyRunner)

    await tw._stop_gateway_http(gateway)
    assert gateway.running is False


@pytest.mark.asyncio
async def test_mcp_reconnect_loop():
    stop_event = asyncio.Event()

    class DummyManager:
        def __init__(self):
            self.clients = {"vscode-copilot": SimpleNamespace(connected=False)}
            self.add_client = AsyncMock()

    gateway = SimpleNamespace(mcp_client_manager=DummyManager())

    # Let it run one iteration and then stop
    async def delayed_stop():
        await asyncio.sleep(0.02)
        stop_event.set()

    asyncio.create_task(delayed_stop())

    await tw._mcp_reconnect_loop(
        gateway, {"reconnect_interval": 0.01, "mcp_url": "ws://mcp"}, stop_event
    )
    assert gateway.mcp_client_manager.add_client.await_count >= 1


@pytest.mark.asyncio
async def test_run_fully_mocked(mock_config_manager, monkeypatch):
    monkeypatch.setattr(tw, "_load_env", MagicMock())

    class DummyChannel:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self):
            pass

        async def stop(self):
            pass

    monkeypatch.setattr(tw, "create_telegram_channel", lambda *a, **kw: DummyChannel())
    monkeypatch.setattr(tw, "MatrixChannelAdapter", DummyChannel)

    class DummyMCPManager:
        def __init__(self):
            self.clients = {}
            self.add_client = AsyncMock()

    import sys

    monkeypatch.setitem(sys.modules, "navig.mcp", SimpleNamespace(MCPClientManager=DummyMCPManager))

    monkeypatch.setattr(tw, "_start_gateway_http", AsyncMock())
    monkeypatch.setattr(tw, "_stop_gateway_http", AsyncMock())
    monkeypatch.setattr(tw, "_mcp_reconnect_loop", AsyncMock())

    class ImmediateEvent:
        def is_set(self):
            return True

        def set(self):
            pass

    monkeypatch.setattr(tw.asyncio, "Event", ImmediateEvent)

    fake_loop = SimpleNamespace(add_signal_handler=MagicMock(side_effect=NotImplementedError))
    monkeypatch.setattr(tw.asyncio, "get_running_loop", lambda: fake_loop)

    # Test full run execution
    await tw._run(port=9999, enable_gateway=True)


def test_main(monkeypatch):
    class MockArgs:
        port = 1234
        no_gateway = False

    parser_mock = MagicMock()
    parser_mock.return_value.parse_args.return_value = MockArgs()
    monkeypatch.setattr(tw.argparse, "ArgumentParser", parser_mock)

    captured = {}

    def _run(coro):
        captured["coro"] = coro
        coro.close()

    monkeypatch.setattr(tw.asyncio, "run", _run)

    tw.main()
    assert captured.get("coro") is not None
