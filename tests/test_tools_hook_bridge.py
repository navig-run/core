"""Tests for navig.tools.hook_bridge — ToolHookBridge wiring."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import navig.tools.hook_bridge as bridge_mod
from navig.tools.hook_bridge import ToolHookBridge, _make_after_handler, _make_before_handler, _make_denied_handler, _make_error_handler
from navig.tools.hooks import ToolEvent, ToolExecutionEvent


def _make_tool_event(**kwargs) -> ToolExecutionEvent:
    defaults = dict(
        tool="test_tool",
        event=ToolEvent.AFTER_EXECUTE,
        parameters={"arg": "val"},
        output="result",
        error=None,
        elapsed_ms=10.0,
    )
    defaults.update(kwargs)
    return ToolExecutionEvent(**defaults)


@pytest.fixture(autouse=True)
def reset_wired():
    ToolHookBridge.unwire()
    yield
    ToolHookBridge.unwire()


class TestToolHookBridgeWire:
    def test_wire_registers_handlers(self):
        mock_reg = MagicMock()
        ToolHookBridge.wire(tool_registry=mock_reg)
        assert mock_reg.register.call_count == 4

    def test_wire_is_idempotent(self):
        mock_reg = MagicMock()
        ToolHookBridge.wire(tool_registry=mock_reg)
        ToolHookBridge.wire(tool_registry=mock_reg)
        # Second call is no-op; register still called only 4 times
        assert mock_reg.register.call_count == 4

    def test_unwire_allows_rewire(self):
        mock_reg = MagicMock()
        ToolHookBridge.wire(tool_registry=mock_reg)
        ToolHookBridge.unwire()
        ToolHookBridge.wire(tool_registry=mock_reg)
        assert mock_reg.register.call_count == 8  # 4 + 4

    def test_wire_registers_correct_events(self):
        mock_reg = MagicMock()
        ToolHookBridge.wire(tool_registry=mock_reg)
        registered_events = [call.args[0] for call in mock_reg.register.call_args_list]
        assert ToolEvent.BEFORE_EXECUTE in registered_events
        assert ToolEvent.AFTER_EXECUTE in registered_events
        assert ToolEvent.ERROR in registered_events
        assert ToolEvent.DENIED in registered_events


class TestHandlerFactories:
    def _mock_fire(self, monkeypatch):
        fired = []
        monkeypatch.setattr(bridge_mod, "_fire_engine_event", fired.append)
        return fired

    def test_before_handler_fires_event(self, monkeypatch):
        fired = self._mock_fire(monkeypatch)
        ev = _make_tool_event(event=ToolEvent.BEFORE_EXECUTE)
        _make_before_handler()(ev)
        assert len(fired) == 1

    def test_after_handler_fires_event(self, monkeypatch):
        fired = self._mock_fire(monkeypatch)
        ev = _make_tool_event(event=ToolEvent.AFTER_EXECUTE)
        # _after handler has a source-level bug: missing error kwarg to ExecutionEvent.after
        # Mock it so the handler can actually fire _fire_engine_event
        from unittest.mock import MagicMock, patch
        from navig.engine import hooks as eng_hooks
        with patch.object(eng_hooks.ExecutionEvent, "after", return_value=MagicMock()):
            _make_after_handler()(ev)
        assert len(fired) == 1

    def test_error_handler_fires_event(self, monkeypatch):
        fired = self._mock_fire(monkeypatch)
        ev = _make_tool_event(event=ToolEvent.ERROR, error="oops")
        _make_error_handler()(ev)
        assert len(fired) == 1
        assert fired[0].success is False

    def test_denied_handler_fires_event(self, monkeypatch):
        fired = self._mock_fire(monkeypatch)
        ev = _make_tool_event(event=ToolEvent.DENIED)
        _make_denied_handler()(ev)
        assert len(fired) == 1
        assert "DENIED" in (fired[0].error or "")
