"""
Batch 86 — navig/core/context.py
Tests for ContextManager.get_active_host and related priority resolution.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from navig.core.context import ContextManager


# ---------------------------------------------------------------------------
# MockProvider helper
# ---------------------------------------------------------------------------


def _make_provider(
    tmp_path: Path,
    *,
    existing_hosts: list[str] | None = None,
    global_config: dict | None = None,
    active_host_content: str | None = None,
    active_app_content: str | None = None,
    local_config: dict | None = None,
    verbose: bool = False,
) -> MagicMock:
    """Build a minimal mock ContextConfigProvider for testing."""
    existing_hosts = existing_hosts or []
    global_config = global_config or {}

    active_host_file = tmp_path / "active_host.txt"
    if active_host_content is not None:
        active_host_file.write_text(active_host_content)

    active_app_file = tmp_path / "active_app.txt"
    if active_app_content is not None:
        active_app_file.write_text(active_app_content)

    provider = MagicMock()
    provider.base_dir = tmp_path
    provider.active_host_file = active_host_file
    provider.active_app_file = active_app_file
    provider.global_config = global_config
    provider.verbose = verbose
    provider.host_exists.side_effect = lambda name: name in existing_hosts
    provider.app_exists.return_value = False
    provider.list_apps.return_value = []
    provider.load_host_config.return_value = {}
    provider.get_local_config.return_value = local_config or {}
    provider.set_local_config.return_value = None
    return provider


# ---------------------------------------------------------------------------
# get_active_host
# ---------------------------------------------------------------------------


class TestGetActiveHostNoSource:
    """Tests for get_active_host() without return_source."""

    def test_returns_none_when_nothing_configured(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NAVIG_ACTIVE_HOST", raising=False)
        provider = _make_provider(tmp_path, existing_hosts=[])
        ctx = ContextManager(provider)
        assert ctx.get_active_host() is None

    def test_env_var_takes_priority(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NAVIG_ACTIVE_HOST", "my-server")
        provider = _make_provider(
            tmp_path,
            existing_hosts=["my-server", "other-server"],
            active_host_content="other-server",
        )
        ctx = ContextManager(provider)
        assert ctx.get_active_host() == "my-server"

    def test_env_var_unknown_host_falls_through(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NAVIG_ACTIVE_HOST", "no-such-host")
        provider = _make_provider(
            tmp_path,
            existing_hosts=["real-host"],
            global_config={"default_host": "real-host"},
        )
        ctx = ContextManager(provider)
        # env host doesn't exist → falls through to default
        assert ctx.get_active_host() == "real-host"

    def test_global_cache_file_used(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NAVIG_ACTIVE_HOST", raising=False)
        provider = _make_provider(
            tmp_path,
            existing_hosts=["cached-host"],
            active_host_content="cached-host",
        )
        ctx = ContextManager(provider)
        assert ctx.get_active_host() == "cached-host"

    def test_cache_file_unknown_host_ignored(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NAVIG_ACTIVE_HOST", raising=False)
        provider = _make_provider(
            tmp_path,
            existing_hosts=["real-host"],
            active_host_content="ghost-host",  # not in existing_hosts
            global_config={"default_host": "real-host"},
        )
        ctx = ContextManager(provider)
        assert ctx.get_active_host() == "real-host"

    def test_default_host_from_global_config(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NAVIG_ACTIVE_HOST", raising=False)
        provider = _make_provider(
            tmp_path,
            existing_hosts=["default-server"],
            global_config={"default_host": "default-server"},
        )
        ctx = ContextManager(provider)
        assert ctx.get_active_host() == "default-server"

    def test_global_config_active_host_fallback(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NAVIG_ACTIVE_HOST", raising=False)
        provider = _make_provider(
            tmp_path,
            existing_hosts=["config-host"],
            global_config={"active_host": "config-host"},
        )
        ctx = ContextManager(provider)
        assert ctx.get_active_host() == "config-host"


class TestGetActiveHostWithSource:
    """Tests for get_active_host(return_source=True)."""

    def test_none_source_when_nothing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NAVIG_ACTIVE_HOST", raising=False)
        provider = _make_provider(tmp_path)
        ctx = ContextManager(provider)
        host, source = ctx.get_active_host(return_source=True)
        assert host is None
        assert source == "none"

    def test_env_source(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NAVIG_ACTIVE_HOST", "env-host")
        provider = _make_provider(tmp_path, existing_hosts=["env-host"])
        ctx = ContextManager(provider)
        host, source = ctx.get_active_host(return_source=True)
        assert host == "env-host"
        assert source == "env"

    def test_user_source_from_cache(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NAVIG_ACTIVE_HOST", raising=False)
        provider = _make_provider(
            tmp_path,
            existing_hosts=["cache-host"],
            active_host_content="cache-host",
        )
        ctx = ContextManager(provider)
        host, source = ctx.get_active_host(return_source=True)
        assert host == "cache-host"
        assert source == "user"

    def test_default_source(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NAVIG_ACTIVE_HOST", raising=False)
        provider = _make_provider(
            tmp_path,
            existing_hosts=["dflt"],
            global_config={"default_host": "dflt"},
        )
        ctx = ContextManager(provider)
        host, source = ctx.get_active_host(return_source=True)
        assert host == "dflt"
        assert source == "default"

    def test_config_source_from_global_active_host(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NAVIG_ACTIVE_HOST", raising=False)
        provider = _make_provider(
            tmp_path,
            existing_hosts=["cfg-host"],
            global_config={"active_host": "cfg-host"},
        )
        ctx = ContextManager(provider)
        host, source = ctx.get_active_host(return_source=True)
        assert host == "cfg-host"
        assert source == "config"


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestContextManagerConstruction:
    def test_instantiation(self, tmp_path):
        provider = _make_provider(tmp_path)
        ctx = ContextManager(provider)
        assert ctx is not None

    def test_config_provider_stored(self, tmp_path):
        provider = _make_provider(tmp_path)
        ctx = ContextManager(provider)
        assert ctx._config is provider
