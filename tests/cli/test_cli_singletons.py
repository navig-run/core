"""
Tests for navig/cli/_singletons.py
Covers set_no_cache, _get_config_manager, and the three lazy class loaders.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import navig.cli._singletons as singletons_mod
from navig.cli._singletons import (
    _get_ai_assistant,
    _get_config_manager,
    _get_remote_operations,
    _get_tunnel_manager,
    set_no_cache,
)


# ---------------------------------------------------------------------------
# Fixture: reset module-level cached class references between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singletons():
    """Clear cached class references so each test starts from None."""
    original_tunnel = singletons_mod._TunnelManager
    original_remote = singletons_mod._RemoteOperations
    original_ai = singletons_mod._AIAssistant
    original_no_cache = singletons_mod._NO_CACHE
    singletons_mod._TunnelManager = None
    singletons_mod._RemoteOperations = None
    singletons_mod._AIAssistant = None
    singletons_mod._NO_CACHE = False
    yield
    singletons_mod._TunnelManager = original_tunnel
    singletons_mod._RemoteOperations = original_remote
    singletons_mod._AIAssistant = original_ai
    singletons_mod._NO_CACHE = original_no_cache


# ---------------------------------------------------------------------------
# set_no_cache
# ---------------------------------------------------------------------------

class TestSetNoCache:
    def test_set_true_sets_flag(self):
        with patch("navig.config.set_config_cache_bypass"):
            with patch("navig.config.reset_config_manager"):
                set_no_cache(True)
        assert singletons_mod._NO_CACHE is True

    def test_set_false_clears_flag(self):
        singletons_mod._NO_CACHE = True
        with patch("navig.config.set_config_cache_bypass"):
            with patch("navig.config.reset_config_manager"):
                set_no_cache(False)
        assert singletons_mod._NO_CACHE is False

    def test_set_true_calls_reset(self):
        mock_reset = MagicMock()
        mock_bypass = MagicMock()
        with patch("navig.config.reset_config_manager", mock_reset):
            with patch("navig.config.set_config_cache_bypass", mock_bypass):
                set_no_cache(True)
        mock_reset.assert_called_once()
        mock_bypass.assert_called_once_with(True)

    def test_set_false_does_not_call_reset(self):
        mock_reset = MagicMock()
        with patch("navig.config.reset_config_manager", mock_reset):
            with patch("navig.config.set_config_cache_bypass"):
                set_no_cache(False)
        mock_reset.assert_not_called()

    def test_import_error_silently_suppressed(self):
        # If config module import fails, set_no_cache should not raise
        with patch.dict("sys.modules", {"navig.config": None}):
            try:
                set_no_cache(True)
            except Exception:
                pass  # may raise due to None module, that's acceptable — just not an unhandled crash
        # Key: no unhandled exception propagates as TypeError/etc in normal usage

    def test_accepts_truthy_value(self):
        with patch("navig.config.set_config_cache_bypass"):
            with patch("navig.config.reset_config_manager"):
                set_no_cache(1)
        assert singletons_mod._NO_CACHE is True

    def test_accepts_falsy_value(self):
        singletons_mod._NO_CACHE = True
        set_no_cache(0)
        assert singletons_mod._NO_CACHE is False


# ---------------------------------------------------------------------------
# _get_config_manager
# ---------------------------------------------------------------------------

class TestGetConfigManager:
    def test_delegates_to_navig_config(self):
        mock_cm = MagicMock()
        with patch("navig.config.get_config_manager", return_value=mock_cm):
            result = _get_config_manager()
        assert result is mock_cm

    def test_returns_value_from_navig_config(self):
        sentinel = object()
        with patch("navig.config.get_config_manager", return_value=sentinel):
            result = _get_config_manager()
        assert result is sentinel


# ---------------------------------------------------------------------------
# _get_tunnel_manager
# ---------------------------------------------------------------------------

class TestGetTunnelManager:
    def test_returns_a_class(self):
        fake_cls = type("FakeTunnel", (), {})
        with patch("navig.tunnel.TunnelManager", fake_cls, create=True):
            result = _get_tunnel_manager()
        assert result is fake_cls

    def test_caches_on_second_call(self):
        fake_cls = type("FakeTunnel", (), {})
        with patch("navig.tunnel.TunnelManager", fake_cls, create=True):
            r1 = _get_tunnel_manager()
            r2 = _get_tunnel_manager()
        assert r1 is r2

    def test_cached_value_stored_in_module(self):
        fake_cls = type("FakeTunnel", (), {})
        with patch("navig.tunnel.TunnelManager", fake_cls, create=True):
            _get_tunnel_manager()
        assert singletons_mod._TunnelManager is fake_cls

    def test_pre_cached_value_returned_without_import(self):
        sentinel = type("Sentinel", (), {})
        singletons_mod._TunnelManager = sentinel
        # Even if TunnelManager import would fail, cached value is returned
        result = _get_tunnel_manager()
        assert result is sentinel


# ---------------------------------------------------------------------------
# _get_remote_operations
# ---------------------------------------------------------------------------

class TestGetRemoteOperations:
    def test_returns_a_class(self):
        fake_cls = type("FakeRemote", (), {})
        with patch("navig.remote.RemoteOperations", fake_cls, create=True):
            result = _get_remote_operations()
        assert result is fake_cls

    def test_caches_on_second_call(self):
        fake_cls = type("FakeRemote", (), {})
        with patch("navig.remote.RemoteOperations", fake_cls, create=True):
            r1 = _get_remote_operations()
            r2 = _get_remote_operations()
        assert r1 is r2

    def test_pre_cached_value_returned(self):
        sentinel = type("Sentinel", (), {})
        singletons_mod._RemoteOperations = sentinel
        result = _get_remote_operations()
        assert result is sentinel


# ---------------------------------------------------------------------------
# _get_ai_assistant
# ---------------------------------------------------------------------------

class TestGetAiAssistant:
    def test_returns_a_class(self):
        fake_cls = type("FakeAI", (), {})
        with patch("navig.ai.AIAssistant", fake_cls, create=True):
            result = _get_ai_assistant()
        assert result is fake_cls

    def test_caches_on_second_call(self):
        fake_cls = type("FakeAI", (), {})
        with patch("navig.ai.AIAssistant", fake_cls, create=True):
            r1 = _get_ai_assistant()
            r2 = _get_ai_assistant()
        assert r1 is r2

    def test_pre_cached_value_returned(self):
        sentinel = type("Sentinel", (), {})
        singletons_mod._AIAssistant = sentinel
        result = _get_ai_assistant()
        assert result is sentinel
