"""Tests for navig-telegram/handler.py"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

# Use importlib to avoid 'handler' name collision with navig-memory's handler.py
_HANDLER_FILE = pathlib.Path(__file__).parent.parent / "handler.py"
_spec = importlib.util.spec_from_file_location("navig_telegram_handler", _HANDLER_FILE)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["navig_telegram_handler"] = _mod
_spec.loader.exec_module(_mod)

PluginContext = _mod.PluginContext
PluginEvent = _mod.PluginEvent
_scoped_src_path = _mod._scoped_src_path
on_event = _mod.on_event
on_load = _mod.on_load
on_unload = _mod.on_unload


# ── Data classes ──────────────────────────────────────────────────────────────

class TestPluginContext:
    def test_basic_creation(self, tmp_path):
        ctx = PluginContext(pack_id="navig-telegram", version="1.0", store_path=tmp_path)
        assert ctx.pack_id == "navig-telegram"
        assert ctx.version == "1.0"
        assert ctx.store_path == tmp_path

    def test_config_defaults_to_empty_dict(self, tmp_path):
        ctx = PluginContext(pack_id="x", version="0", store_path=tmp_path)
        assert ctx.config == {}

    def test_config_accepts_dict(self, tmp_path):
        ctx = PluginContext(pack_id="x", version="0", store_path=tmp_path, config={"token": "abc"})
        assert ctx.config["token"] == "abc"


class TestPluginEvent:
    def test_basic_creation(self):
        ev = PluginEvent(name="navig.start", payload={"key": "val"}, source="runtime")
        assert ev.name == "navig.start"
        assert ev.payload == {"key": "val"}
        assert ev.source == "runtime"


# ── _scoped_src_path ──────────────────────────────────────────────────────────

class TestScopedSrcPath:
    def test_adds_and_removes_src_from_path(self, tmp_path):
        src = str(tmp_path / "src")
        assert src not in sys.path
        with _scoped_src_path(tmp_path):
            assert src in sys.path
        assert src not in sys.path

    def test_does_not_double_add_if_already_present(self, tmp_path):
        src = str(tmp_path / "src")
        sys.path.insert(0, src)
        try:
            count_before = sys.path.count(src)
            with _scoped_src_path(tmp_path):
                assert sys.path.count(src) == count_before
            # Should not remove since we did not add it
            assert src in sys.path
        finally:
            sys.path.remove(src)

    def test_removes_even_if_body_raises(self, tmp_path):
        src = str(tmp_path / "src")
        with pytest.raises(ValueError):
            with _scoped_src_path(tmp_path):
                assert src in sys.path
                raise ValueError("boom")
        assert src not in sys.path


# ── on_event ──────────────────────────────────────────────────────────────────

class TestOnEvent:
    def test_returns_none(self, tmp_path):
        ctx = PluginContext(pack_id="navig-telegram", version="1.0", store_path=tmp_path)
        ev = PluginEvent(name="navig.ready", payload={}, source="runtime")
        assert on_event(ev, ctx) is None

    def test_returns_none_for_any_event(self, tmp_path):
        ctx = PluginContext(pack_id="navig-telegram", version="1.0", store_path=tmp_path)
        for name in ("navig.start", "navig.stop", "navig.notification", "unknown"):
            ev = PluginEvent(name=name, payload={}, source="test")
            assert on_event(ev, ctx) is None


# ── on_load ───────────────────────────────────────────────────────────────────

class TestOnLoad:
    def test_starts_worker_on_success(self, tmp_path):
        ctx = PluginContext(pack_id="navig-telegram", version="1.0", store_path=tmp_path)
        mock_worker = MagicMock()
        mock_worker.start = MagicMock()
        with patch.dict(sys.modules, {"telegram_worker": mock_worker}):
            on_load(ctx)
        mock_worker.start.assert_called_once_with(ctx.config, tmp_path.parent)

    def test_raises_runtime_error_on_import_error(self, tmp_path):
        ctx = PluginContext(pack_id="navig-telegram", version="1.0", store_path=tmp_path)
        # Ensure telegram_worker is not cached correctly
        with patch.dict(sys.modules, {"telegram_worker": None}):
            with pytest.raises((RuntimeError, ImportError)):
                on_load(ctx)

    def test_reraises_worker_exception(self, tmp_path):
        ctx = PluginContext(pack_id="navig-telegram", version="1.0", store_path=tmp_path)
        mock_worker = MagicMock()
        mock_worker.start.side_effect = RuntimeError("worker crashed")
        with patch.dict(sys.modules, {"telegram_worker": mock_worker}):
            with pytest.raises(RuntimeError, match="worker crashed"):
                on_load(ctx)


# ── on_unload ─────────────────────────────────────────────────────────────────

class TestOnUnload:
    def test_stops_worker(self, tmp_path):
        ctx = PluginContext(pack_id="navig-telegram", version="1.0", store_path=tmp_path)
        mock_worker = MagicMock()
        mock_worker.stop = MagicMock()
        with patch.dict(sys.modules, {"telegram_worker": mock_worker}):
            on_unload(ctx)
        mock_worker.stop.assert_called_once()

    def test_does_not_raise_on_worker_exception(self, tmp_path):
        ctx = PluginContext(pack_id="navig-telegram", version="1.0", store_path=tmp_path)
        mock_worker = MagicMock()
        mock_worker.stop.side_effect = RuntimeError("stop failed")
        with patch.dict(sys.modules, {"telegram_worker": mock_worker}):
            on_unload(ctx)  # must not raise

    def test_does_not_raise_on_import_error(self, tmp_path):
        ctx = PluginContext(pack_id="navig-telegram", version="1.0", store_path=tmp_path)
        with patch.dict(sys.modules, {"telegram_worker": None}):
            on_unload(ctx)  # must not raise
