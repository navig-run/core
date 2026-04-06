from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_load_env_prefers_cwd_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from navig.daemon import telegram_worker as tw

    env_path = tmp_path / ".env"
    env_path.write_text("X=1")

    loaded = []
    dotenv_mod = SimpleNamespace(load_dotenv=lambda path: loaded.append(Path(path)))
    monkeypatch.setitem(sys.modules, "dotenv", dotenv_mod)
    monkeypatch.setattr(tw.Path, "cwd", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(tw, "NAVIG_HOME", tmp_path / "navig-home")

    tw._load_env()
    assert loaded
    assert loaded[0] == env_path


def test_telegram_and_deck_config(monkeypatch: pytest.MonkeyPatch):
    from navig.daemon import telegram_worker as tw

    cfg = {
        "telegram": {
            "bot_token": "cfg-token",
            "allowed_users": [1],
            "allowed_groups": [10],
            "require_auth": False,
        },
        "deck": {
            "enabled": False,
            "port": 3000,
            "bind": "0.0.0.0",
            "dev_mode": True,
            "auth_max_age": 120,
        },
    }
    monkeypatch.setattr(tw, "get_config_manager", lambda: SimpleNamespace(global_config=cfg))
    monkeypatch.setattr(tw, "resolve_telegram_bot_token", lambda _cfg=None: "env-token")

    tg = tw._telegram_config()
    dk = tw._deck_config()

    assert tg["bot_token"] == "env-token"
    assert tg["allowed_users"] == [1]
    assert tg["require_auth"] is False
    assert dk["enabled"] is False
    assert dk["port"] == 3000
    assert dk["bind"] == "0.0.0.0"
    assert dk["dev_mode"] is True
    assert dk["auth_max_age"] == 120


def test_main_parses_args_and_runs(monkeypatch: pytest.MonkeyPatch):
    from navig.daemon import telegram_worker as tw

    captured = {}

    def _run(coro):
        captured["coro"] = coro
        coro.close()

    monkeypatch.setattr(tw.asyncio, "run", _run)
    monkeypatch.setattr(sys, "argv", ["telegram_worker.py", "--port", "9999", "--no-gateway"])

    tw.main()
    assert captured["coro"] is not None


def test_telegram_config_without_env(monkeypatch: pytest.MonkeyPatch):
    from navig.daemon import telegram_worker as tw

    monkeypatch.setattr(
        tw,
        "get_config_manager",
        lambda: SimpleNamespace(global_config={"telegram": {"bot_token": "cfg-only"}}),
    )
    monkeypatch.setattr(tw, "resolve_telegram_bot_token", lambda _cfg=None: "cfg-only")
    result = tw._telegram_config()
    assert result["bot_token"] == "cfg-only"


# ── _transport_for_url ────────────────────────────────────────────────────────


class TestTransportForUrl:
    """_transport_for_url maps URL scheme to MCP transport type."""

    def _fn(self):
        from navig.daemon.telegram_worker import _transport_for_url

        return _transport_for_url

    def test_http_maps_to_sse(self):
        assert self._fn()("http://localhost:8080/mcp") == "sse"

    def test_https_maps_to_sse(self):
        assert self._fn()("https://myserver.example.com/mcp") == "sse"

    def test_ws_maps_to_websocket(self):
        assert self._fn()("ws://localhost:9000/ws") == "websocket"

    def test_wss_maps_to_websocket(self):
        assert self._fn()("wss://secure.example.com/ws") == "websocket"

    def test_unknown_scheme_falls_back_to_stdio(self):
        # e.g. an old "ws://" was previously caught by the "http" substring
        # check — now it must land cleanly in websocket; an unknown scheme
        # like "tcp://" should fall through to stdio
        assert self._fn()("tcp://some-host:1234") == "stdio"

    def test_empty_string_falls_back_to_stdio(self):
        assert self._fn()("") == "stdio"

    def test_case_insensitive(self):
        # URLs are lowercased internally
        assert self._fn()("HTTP://HOST/mcp") == "sse"
        assert self._fn()("WS://HOST/ws") == "websocket"
