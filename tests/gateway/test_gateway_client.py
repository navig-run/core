"""Hermetic unit tests for navig.gateway_client."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


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

        assert port == 8789
        assert host == "127.0.0.1"

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

        assert port == 8789

    def test_import_exception_returns_defaults(self):
        from navig.gateway_client import gateway_cli_defaults

        with patch("navig.config.get_config_manager", side_effect=RuntimeError("boom")):
            port, host = gateway_cli_defaults()

        assert port == 8789
        assert host == "127.0.0.1"


# ---------------------------------------------------------------------------
# gateway_base_url
# ---------------------------------------------------------------------------


class TestGatewayBaseUrl:
    def test_default_url(self):
        from navig.gateway_client import gateway_base_url

        with _patch_config({}):
            url = gateway_base_url()

        assert url == "http://127.0.0.1:8789"

    def test_custom_host_and_port(self):
        from navig.gateway_client import gateway_base_url

        raw = {"gateway": {"host": "192.168.1.100", "port": 9000}}
        with _patch_config(raw):
            url = gateway_base_url()

        assert url == "http://192.168.1.100:9000"


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
