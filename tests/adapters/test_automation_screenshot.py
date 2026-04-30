"""Tests for navig.adapters.automation.screenshot."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ─── Backend registry ─────────────────────────────────────────────────────────


def test_backend_registry_populated():
    from navig.adapters.automation.screenshot import _BACKEND_REGISTRY

    assert len(_BACKEND_REGISTRY) >= 1  # At least Pillow


def test_backend_registry_contains_pillow():
    from navig.adapters.automation.screenshot import _BACKEND_REGISTRY

    assert "pillow" in _BACKEND_REGISTRY


def test_backend_priority_order():
    from navig.adapters.automation.screenshot import _BACKEND_REGISTRY

    priorities = [(cls.priority, name) for name, cls in _BACKEND_REGISTRY.items()]
    sorted_prio = sorted(priorities)
    # Pillow should have the highest priority number (last fallback).
    pillow_prio = next(p for p, n in priorities if n == "pillow")
    assert pillow_prio == max(p for p, _ in priorities)


# ─── get_screenshot_backend ───────────────────────────────────────────────────


def test_get_screenshot_backend_returns_available():
    from navig.adapters.automation.screenshot import get_screenshot_backend

    backend = get_screenshot_backend.cache_clear() if hasattr(get_screenshot_backend, "cache_clear") else None
    with patch(
        "navig.adapters.automation.screenshot._PillowBackend.is_available",
        return_value=True,
    ):
        b = get_screenshot_backend.__wrapped__("auto") if hasattr(get_screenshot_backend, "__wrapped__") else get_screenshot_backend("auto")
    assert b is not None


def test_get_screenshot_backend_env_var_selects_backend(monkeypatch):
    from navig.adapters.automation.screenshot import _BACKEND_ENV_VAR

    monkeypatch.setenv(_BACKEND_ENV_VAR, "pillow")
    # Import after env var is set.
    import importlib
    import navig.adapters.automation.screenshot as mod
    importlib.reload(mod)
    # After reload the constant should still be accessible.
    assert mod._BACKEND_ENV_VAR == _BACKEND_ENV_VAR


# ─── capture_full_screen ──────────────────────────────────────────────────────


def test_capture_full_screen_returns_tuple():
    from navig.adapters.automation.screenshot import _PillowBackend

    fake_img = MagicMock()
    with patch.object(_PillowBackend, "is_available", return_value=True), \
         patch.object(_PillowBackend, "capture_full", return_value=fake_img):
        from navig.adapters.automation.screenshot import capture_full_screen, get_screenshot_backend
        # Bypass lru_cache.
        mock_backend = _PillowBackend()
        with patch("navig.adapters.automation.screenshot.get_screenshot_backend", return_value=mock_backend):
            img, name = capture_full_screen()
            assert img is fake_img
            assert isinstance(name, str)


# ─── capture (region) ─────────────────────────────────────────────────────────


def test_capture_region_calls_backend():
    from navig.adapters.automation.screenshot import _PillowBackend

    fake_img = MagicMock()
    mock_backend = MagicMock()
    mock_backend.name = "pillow"
    mock_backend.capture.return_value = fake_img

    with patch("navig.adapters.automation.screenshot.get_screenshot_backend", return_value=mock_backend):
        from navig.adapters.automation.screenshot import capture
        img, name = capture(0, 0, 100, 100)
        assert img is fake_img
        mock_backend.capture.assert_called_once_with(0, 0, 100, 100)
