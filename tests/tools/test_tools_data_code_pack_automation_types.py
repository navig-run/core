"""Batch 61 — tools/data_pack, tools/code_pack, adapters/automation/types."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.tools.domains.data_pack — _json_parse, register_tools
# ---------------------------------------------------------------------------

class TestJsonParse:
    def _parse(self, text, **kw):
        from navig.tools.domains.data_pack import _json_parse
        return _json_parse(text, **kw)

    def test_valid_object(self):
        result = self._parse('{"a": 1, "b": 2}')
        assert result == {"parsed": {"a": 1, "b": 2}}

    def test_valid_array(self):
        result = self._parse('[1, 2, 3]')
        assert result == {"parsed": [1, 2, 3]}

    def test_null_json(self):
        result = self._parse("null")
        assert result == {"parsed": None}

    def test_number_json(self):
        result = self._parse("42")
        assert result == {"parsed": 42}

    def test_invalid_json_returns_error(self):
        result = self._parse("not valid json")
        assert "error" in result
        assert "Invalid JSON" in result["error"]

    def test_empty_string_returns_error(self):
        result = self._parse("")
        assert "error" in result

    def test_truncated_json_returns_error(self):
        result = self._parse('{"key":')
        assert "error" in result


class TestDataPackRegisterTools:
    def test_register_called_once(self):
        from navig.tools.domains.data_pack import register_tools
        mock_registry = MagicMock()
        register_tools(mock_registry)
        mock_registry.register.assert_called_once()

    def test_registers_json_parse(self):
        from navig.tools.domains.data_pack import register_tools
        mock_registry = MagicMock()
        register_tools(mock_registry)
        call_kwargs = mock_registry.register.call_args
        meta = call_kwargs[0][0]  # first positional arg is ToolMeta
        assert meta.name == "json_parse"

    def test_handler_is_json_parse_fn(self):
        from navig.tools.domains.data_pack import _json_parse, register_tools
        mock_registry = MagicMock()
        register_tools(mock_registry)
        call_kwargs = mock_registry.register.call_args[1]
        assert call_kwargs["handler"] is _json_parse

    def test_json_parse_is_safe(self):
        from navig.tools.domains.data_pack import register_tools
        from navig.tools.router import SafetyLevel
        mock_registry = MagicMock()
        register_tools(mock_registry)
        meta = mock_registry.register.call_args[0][0]
        assert meta.safety == SafetyLevel.SAFE


# ---------------------------------------------------------------------------
# navig.tools.domains.code_pack — register_tools
# ---------------------------------------------------------------------------

class TestCodePackRegisterTools:
    def test_register_called_once(self):
        from navig.tools.domains.code_pack import register_tools
        mock_registry = MagicMock()
        register_tools(mock_registry)
        mock_registry.register.assert_called_once()

    def test_registers_code_sandbox(self):
        from navig.tools.domains.code_pack import register_tools
        mock_registry = MagicMock()
        register_tools(mock_registry)
        meta = mock_registry.register.call_args[0][0]
        assert meta.name == "code_sandbox"

    def test_code_sandbox_is_dangerous(self):
        from navig.tools.domains.code_pack import register_tools
        from navig.tools.router import SafetyLevel
        mock_registry = MagicMock()
        register_tools(mock_registry)
        meta = mock_registry.register.call_args[0][0]
        assert meta.safety == SafetyLevel.DANGEROUS

    def test_has_code_parameter(self):
        from navig.tools.domains.code_pack import register_tools
        mock_registry = MagicMock()
        register_tools(mock_registry)
        meta = mock_registry.register.call_args[0][0]
        assert "code" in meta.parameters_schema


# ---------------------------------------------------------------------------
# navig.adapters.automation.types — ExecutionResult, WindowInfo
# ---------------------------------------------------------------------------

class TestExecutionResult:
    def test_success_true(self):
        from navig.adapters.automation.types import ExecutionResult
        r = ExecutionResult(success=True)
        assert r.success is True

    def test_defaults(self):
        from navig.adapters.automation.types import ExecutionResult
        r = ExecutionResult(success=False)
        assert r.stdout == ""
        assert r.stderr == ""
        assert r.exit_code == 0
        assert r.duration_seconds == 0.0
        assert r.status == "COMPLETED"

    def test_custom_values(self):
        from navig.adapters.automation.types import ExecutionResult
        r = ExecutionResult(
            success=True, stdout="out", stderr="err",
            exit_code=1, duration_seconds=0.5, status="FAILED"
        )
        assert r.stdout == "out"
        assert r.stderr == "err"
        assert r.exit_code == 1
        assert r.duration_seconds == 0.5
        assert r.status == "FAILED"


class TestWindowInfo:
    def _make(self, **overrides):
        from navig.adapters.automation.types import WindowInfo
        defaults = dict(
            title="Test Window", id="0x1234", pid=1234,
            class_name="TestClass", x=0, y=0, width=800, height=600
        )
        defaults.update(overrides)
        return WindowInfo(**defaults)

    def test_to_dict_has_required_keys(self):
        w = self._make()
        d = w.to_dict()
        for key in ("title", "id", "pid", "class_name", "x", "y", "width", "height", "state"):
            assert key in d

    def test_state_normal_by_default(self):
        w = self._make()
        assert w.to_dict()["state"] == "normal"

    def test_state_minimized(self):
        w = self._make(is_minimized=True)
        assert w.to_dict()["state"] == "minimized"

    def test_state_maximized(self):
        w = self._make(is_maximized=True)
        assert w.to_dict()["state"] == "maximized"

    def test_minimized_takes_priority_over_maximized(self):
        # is_minimized wins when both are True
        w = self._make(is_minimized=True, is_maximized=True)
        assert w.to_dict()["state"] == "minimized"

    def test_title_in_dict(self):
        w = self._make(title="My App")
        assert w.to_dict()["title"] == "My App"

    def test_process_name_default_none(self):
        w = self._make()
        assert w.to_dict()["process_name"] is None

    def test_process_name_propagated(self):
        w = self._make(process_name="notepad.exe")
        assert w.to_dict()["process_name"] == "notepad.exe"
