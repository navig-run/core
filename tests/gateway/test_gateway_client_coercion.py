"""Tests for gateway client numeric coercion configuration fallback."""

import pytest

from navig.gateway_client import gateway_cli_defaults


def test_gateway_cli_defaults_coerces_valid_port(monkeypatch):
    class FakeConfigManager:
        def _load_global_config(self):
            return {"gateway": {"port": "9001", "host": "example.com"}}

    monkeypatch.setattr("navig.config.get_config_manager", lambda: FakeConfigManager())
    port, host = gateway_cli_defaults()
    assert port == 9001
    assert host == "example.com"

def test_gateway_cli_defaults_falls_back_on_malformed_port(monkeypatch):
    class FakeConfigManager:
        def _load_global_config(self):
            return {"gateway": {"port": "invalid-port", "host": "127.0.0.1"}}

    monkeypatch.setattr("navig.config.get_config_manager", lambda: FakeConfigManager())
    port, host = gateway_cli_defaults()
    assert port == 8789  # Fallback to default
    assert host == "127.0.0.1"

def test_gateway_cli_defaults_falls_back_on_none_port(monkeypatch):
    class FakeConfigManager:
        def _load_global_config(self):
            return {"gateway": {"port": None, "host": "127.0.0.1"}}

    monkeypatch.setattr("navig.config.get_config_manager", lambda: FakeConfigManager())
    port, host = gateway_cli_defaults()
    assert port == 8789  # Fallback to default
    assert host == "127.0.0.1"
