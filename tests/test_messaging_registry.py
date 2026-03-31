from __future__ import annotations

from navig.messaging.registry import (
    get_active_provider_name,
    is_supported_provider_name,
    supported_provider_names,
)


def test_get_active_provider_name_prefers_env(monkeypatch):
    monkeypatch.setenv("NAVIG_MESSAGING_PROVIDER", "none")
    cfg = {"messaging": {"provider": "telegram"}}
    assert get_active_provider_name(cfg) == "none"


def test_get_active_provider_name_reads_config_when_env_missing(monkeypatch):
    monkeypatch.delenv("NAVIG_MESSAGING_PROVIDER", raising=False)
    cfg = {"messaging": {"provider": "telegram"}}
    assert get_active_provider_name(cfg) == "telegram"


def test_supported_provider_helpers():
    names = supported_provider_names()
    assert "telegram" in names
    assert "none" in names
    assert is_supported_provider_name("telegram") is True
    assert is_supported_provider_name("none") is True
    assert is_supported_provider_name("matrix") is False
