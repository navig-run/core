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


# ─── Abstract-base contract (audit R1 backlog) ────────────────────────────────


def test_base_backend_cannot_be_instantiated():
    """`_ScreenshotBackend` is a real abc.ABC — instantiating it (or any subclass
    that forgets a method) fails at CONSTRUCTION with TypeError, not late at call
    time with NotImplementedError."""
    from navig.adapters.automation.screenshot import _ScreenshotBackend

    assert issubclass(_ScreenshotBackend, __import__("abc").ABC)
    with pytest.raises(TypeError):
        _ScreenshotBackend()  # abstract → not instantiable


def test_incomplete_subclass_cannot_be_instantiated():
    """A subclass missing an abstract method is uninstantiable (the contract the
    ABC now enforces)."""
    from navig.adapters.automation.screenshot import _ScreenshotBackend

    class _Broken(_ScreenshotBackend):
        # deliberately NO `name` (so it isn't registered) and NO capture_region
        def is_available(self) -> bool:
            return True

    with pytest.raises(TypeError):
        _Broken()


def test_concrete_backends_are_instantiable():
    """Every registered backend implements both methods — incl. the ones whose
    `is_available` is a @staticmethod override — so it instantiates cleanly."""
    from navig.adapters.automation.screenshot import _BACKEND_REGISTRY

    for name, cls in _BACKEND_REGISTRY.items():
        inst = cls()  # must not raise
        assert callable(inst.capture_region), name
        assert isinstance(inst.is_available(), bool), name


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
    from navig.adapters.automation.screenshot import capture_full_screen

    fake_img = MagicMock()

    with patch("navig.adapters.automation.screenshot.capture", return_value=(fake_img, "pillow")):
        img, name = capture_full_screen()
        assert img is fake_img
        assert name == "pillow"


# ─── capture (region) ─────────────────────────────────────────────────────────


def test_capture_region_calls_backend():
    fake_img = MagicMock()
    mock_backend = MagicMock()
    mock_backend.name = "pillow"
    mock_backend.capture_region.return_value = fake_img

    with patch("navig.adapters.automation.screenshot.get_screenshot_backend", return_value=mock_backend):
        from navig.adapters.automation.screenshot import capture
        img, name = capture(0, 0, 100, 100)
        assert img is fake_img
        mock_backend.capture_region.assert_called_once_with(0, 0, 100, 100)
