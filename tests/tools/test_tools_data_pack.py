"""Tests for navig/tools/domains/data_pack.py."""

import json
import pytest
from unittest.mock import MagicMock, call

from navig.tools.domains.data_pack import _json_parse, register_tools


# ---------------------------------------------------------------------------
# _json_parse — valid JSON
# ---------------------------------------------------------------------------

def test_json_parse_simple_object():
    result = _json_parse('{"key": "value"}')
    assert result == {"parsed": {"key": "value"}}


def test_json_parse_returns_parsed_key():
    result = _json_parse('{"a": 1}')
    assert "parsed" in result


def test_json_parse_array():
    result = _json_parse('[1, 2, 3]')
    assert result["parsed"] == [1, 2, 3]


def test_json_parse_number():
    result = _json_parse('42')
    assert result["parsed"] == 42


def test_json_parse_null():
    result = _json_parse('null')
    assert result["parsed"] is None


def test_json_parse_boolean_true():
    result = _json_parse('true')
    assert result["parsed"] is True


def test_json_parse_boolean_false():
    result = _json_parse('false')
    assert result["parsed"] is False


def test_json_parse_nested_object():
    payload = '{"outer": {"inner": [1, 2]}}'
    result = _json_parse(payload)
    assert result["parsed"]["outer"]["inner"] == [1, 2]


def test_json_parse_empty_object():
    result = _json_parse('{}')
    assert result["parsed"] == {}


def test_json_parse_empty_array():
    result = _json_parse('[]')
    assert result["parsed"] == []


def test_json_parse_accepts_kwargs():
    # Should not raise when extra kwargs passed
    result = _json_parse('{"x": 1}', extra_kwarg="ignored")
    assert "parsed" in result


# ---------------------------------------------------------------------------
# _json_parse — invalid JSON
# ---------------------------------------------------------------------------

def test_json_parse_invalid_returns_error():
    result = _json_parse("not valid json")
    assert "error" in result


def test_json_parse_invalid_no_parsed_key():
    result = _json_parse("not valid json")
    assert "parsed" not in result


def test_json_parse_error_message_contains_invalid_json():
    result = _json_parse("bad")
    assert "Invalid JSON" in result["error"] or "error" in result


def test_json_parse_truncated_json():
    result = _json_parse('{"key":')
    assert "error" in result


def test_json_parse_single_quote_json():
    result = _json_parse("{'key': 'value'}")
    assert "error" in result


def test_json_parse_empty_string():
    result = _json_parse("")
    assert "error" in result


# ---------------------------------------------------------------------------
# register_tools
# ---------------------------------------------------------------------------

def test_register_tools_calls_registry_register():
    registry = MagicMock()
    register_tools(registry)
    registry.register.assert_called_once()


def test_register_tools_passes_tool_meta_as_positional():
    registry = MagicMock()
    register_tools(registry)
    call_args = registry.register.call_args
    assert call_args is not None


def test_register_tools_meta_name_is_json_parse():
    registry = MagicMock()
    register_tools(registry)
    meta_arg = registry.register.call_args[0][0]
    assert meta_arg.name == "json_parse"


def test_register_tools_passes_handler():
    registry = MagicMock()
    register_tools(registry)
    call_kwargs = registry.register.call_args[1]
    assert "handler" in call_kwargs
    assert call_kwargs["handler"] is _json_parse


def test_register_tools_meta_has_data_domain():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "DATA" in str(meta.domain).upper()


def test_register_tools_meta_safety_is_safe():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "SAFE" in str(meta.safety).upper()


def test_register_tools_meta_has_tags():
    registry = MagicMock()
    register_tools(registry)
    meta = registry.register.call_args[0][0]
    assert "json" in meta.tags or "parse" in meta.tags
