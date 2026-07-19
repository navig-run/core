"""Hermetic unit tests for navig.gateway_client."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig._daemon_defaults import _DAEMON_PORT, _GATEWAY_PORT


def _make_config_mock(raw: dict):
    mgr = MagicMock()
    mgr._load_global_config.return_value = raw
    return mgr


def _patch_config(raw: dict):
    """Patch navig.config.get_config_manager at the source (lazy import target)."""
    return patch("navig.config.get_config_manager", return_value=_make_config_mock(raw))


# ---------------------------------------------------------------------------
# gateway_cli_defaults
# ---------------------------------------------------------------------------


class TestGatewayCliDefaults:
    def test_returns_default_port_and_host_on_empty_config(self):
        from navig.gateway_client import gateway_cli_defaults

        with _patch_config({}):
            port, host = gateway_cli_defaults()

        # Canonical gateway default is 8789 (NOT the daemon-IPC port 8765).
        assert port == _GATEWAY_PORT == 8789
        assert host == "127.0.0.1"

    def test_default_port_never_collides_with_daemon_ipc(self):
        # Regression guard: the gateway must never default to the IPC/MCP daemon
        # port, or it squats the daemon and 8789-probing clients can't reach it.
        assert _GATEWAY_PORT != _DAEMON_PORT

    def test_reads_port_and_host_from_config(self):
        from navig.gateway_client import gateway_cli_defaults

        raw = {"gateway": {"port": 9090, "host": "0.0.0.0"}}
        with _patch_config(raw):
            port, host = gateway_cli_defaults()

        assert port == 9090
        assert host == "0.0.0.0"

    def test_handles_string_port_coerces_to_int(self):
        from navig.gateway_client import gateway_cli_defaults

        raw = {"gateway": {"port": "7777"}}
        with _patch_config(raw):
            port, _ = gateway_cli_defaults()

        assert port == 7777

    def test_invalid_port_falls_back_to_default(self):
        from navig.gateway_client import gateway_cli_defaults

        raw = {"gateway": {"port": "not-a-number"}}
        with _patch_config(raw):
            port, _ = gateway_cli_defaults()

        assert port == _GATEWAY_PORT == 8789

    def test_import_exception_returns_defaults(self):
        from navig.gateway_client import gateway_cli_defaults

        with patch("navig.config.get_config_manager", side_effect=RuntimeError("boom")):
            port, host = gateway_cli_defaults()

        assert port == _GATEWAY_PORT == 8789
        assert host == "127.0.0.1"


# ---------------------------------------------------------------------------
# gateway_base_url
# ---------------------------------------------------------------------------


class TestGatewayBaseUrl:
    # gateway_base_url() is discovery-aware: isolate NAVIG_CONFIG_DIR so a real
    # ~/.navig/gateway.json on the dev machine can't leak into these tests.

    def test_default_url(self, tmp_path, monkeypatch):
        from navig.gateway_client import gateway_base_url

        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        with _patch_config({}):
            url = gateway_base_url()

        assert url == "http://127.0.0.1:8789"

    def test_custom_host_and_port(self, tmp_path, monkeypatch):
        from navig.gateway_client import gateway_base_url

        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        raw = {"gateway": {"host": "192.168.1.100", "port": 9000}}
        with _patch_config(raw):
            url = gateway_base_url()

        assert url == "http://192.168.1.100:9000"

    def test_bind_any_host_maps_to_loopback(self, tmp_path, monkeypatch):
        # gateway.host=0.0.0.0 is a BIND address; a client can't connect to it.
        from navig.gateway_client import gateway_base_url

        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        raw = {"gateway": {"host": "0.0.0.0", "port": 9000}}
        with _patch_config(raw):
            url = gateway_base_url()

        assert url == "http://127.0.0.1:9000"

    def test_follows_live_discovery(self, tmp_path, monkeypatch):
        # The self-healed port from gateway.json wins when its endpoint is live.
        from navig.gateway_client import gateway_base_url

        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        _write_discovery(tmp_path, {"host": "127.0.0.1", "port": 56564})

        with _patch_config({}), patch("socket.create_connection"):
            url = gateway_base_url()

        assert url == "http://127.0.0.1:56564"


# ---------------------------------------------------------------------------
# read_gateway_discovery / gateway_live_defaults
# ---------------------------------------------------------------------------


def _write_discovery(tmp_path, payload) -> None:
    import json

    (tmp_path / "gateway.json").write_text(json.dumps(payload), encoding="utf-8")


class TestGatewayDiscovery:
    def test_reads_discovery_file(self, tmp_path, monkeypatch):
        from navig.gateway_client import read_gateway_discovery

        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        _write_discovery(tmp_path, {"host": "127.0.0.1", "port": 56564})

        assert read_gateway_discovery() == (56564, "127.0.0.1")

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        from navig.gateway_client import read_gateway_discovery

        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))

        assert read_gateway_discovery() is None

    def test_malformed_file_returns_none(self, tmp_path, monkeypatch):
        from navig.gateway_client import read_gateway_discovery

        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        (tmp_path / "gateway.json").write_text("{not json", encoding="utf-8")

        assert read_gateway_discovery() is None

    def test_invalid_port_returns_none(self, tmp_path, monkeypatch):
        from navig.gateway_client import read_gateway_discovery

        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        _write_discovery(tmp_path, {"host": "127.0.0.1", "port": 0})

        assert read_gateway_discovery() is None


class TestGatewayLiveDefaults:
    def test_prefers_live_discovery_over_config(self, tmp_path, monkeypatch):
        # The self-healing bind landed the gateway on 56564 (config says 8789):
        # a client resolving the gateway must follow the discovery file.
        from navig.gateway_client import gateway_live_defaults

        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        _write_discovery(tmp_path, {"host": "127.0.0.1", "port": 56564})

        with _patch_config({}), patch("socket.create_connection") as conn:
            port, host = gateway_live_defaults()

        assert (port, host) == (56564, "127.0.0.1")
        conn.assert_called_once()

    def test_stale_discovery_falls_back_to_config(self, tmp_path, monkeypatch):
        # Discovery file exists but nothing listens there (daemon down/moved):
        # fall back to the configured port rather than a dead endpoint.
        from navig.gateway_client import gateway_live_defaults

        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        _write_discovery(tmp_path, {"host": "127.0.0.1", "port": 56564})

        with (
            _patch_config({"gateway": {"port": 9090}}),
            patch("socket.create_connection", side_effect=ConnectionRefusedError),
        ):
            port, host = gateway_live_defaults()

        assert (port, host) == (9090, "127.0.0.1")

    def test_no_discovery_uses_config_without_probe(self, tmp_path, monkeypatch):
        from navig.gateway_client import gateway_live_defaults

        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))

        with _patch_config({}), patch("socket.create_connection") as conn:
            port, host = gateway_live_defaults()

        assert (port, host) == (8789, "127.0.0.1")
        conn.assert_not_called()


# ---------------------------------------------------------------------------
# gateway_request_headers
# ---------------------------------------------------------------------------


class TestGatewayRequestHeaders:
    def test_no_token_returns_only_actor_header(self):
        from navig.gateway_client import gateway_request_headers

        with _patch_config({}):
            headers = gateway_request_headers()

        assert headers == {"X-Actor": "navig-cli"}
        assert "Authorization" not in headers

    def test_auth_token_in_auth_sub_key(self):
        from navig.gateway_client import gateway_request_headers

        raw = {"gateway": {"auth": {"token": "secret123"}}}
        with _patch_config(raw):
            headers = gateway_request_headers()

        assert headers["Authorization"] == "Bearer secret123"

    def test_legacy_auth_token_key(self):
        from navig.gateway_client import gateway_request_headers

        raw = {"gateway": {"auth_token": "legacy_tok"}}
        with _patch_config(raw):
            headers = gateway_request_headers()

        assert headers["Authorization"] == "Bearer legacy_tok"

    def test_config_exception_returns_actor_header_only(self):
        from navig.gateway_client import gateway_request_headers

        with patch("navig.config.get_config_manager", side_effect=Exception("fail")):
            headers = gateway_request_headers()

        assert headers == {"X-Actor": "navig-cli"}
