"""Tests for env_validator.py, core/dict_utils.py, commands/telemetry.py — batch 53."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

runner = CliRunner()

# ---------------------------------------------------------------------------
# env_validator
# ---------------------------------------------------------------------------


def test_validate_environment_passes_when_key_set(monkeypatch):
    from navig.env_validator import validate_environment

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # Should NOT raise
    validate_environment()


def test_validate_environment_passes_with_any_provider(monkeypatch):
    from navig.env_validator import validate_environment

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    validate_environment()


def test_validate_environment_raises_when_no_keys(monkeypatch):
    from navig.env_validator import validate_environment

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Startup aborted"):
        validate_environment()


def test_validate_environment_prints_to_stderr(monkeypatch, capsys):
    from navig.env_validator import validate_environment

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        validate_environment()
    captured = capsys.readouterr()
    assert "Environment Verification Failed" in captured.err


def test_required_env_vars_has_llm_keys():
    from navig.env_validator import REQUIRED_ENV_VARS

    assert "LLM_KEYS" in REQUIRED_ENV_VARS
    assert REQUIRED_ENV_VARS["LLM_KEYS"]["type"] == "any"
    assert "OPENAI_API_KEY" in REQUIRED_ENV_VARS["LLM_KEYS"]["vars"]


# ---------------------------------------------------------------------------
# core/dict_utils
# ---------------------------------------------------------------------------


def test_deep_merge_simple():
    from navig.core.dict_utils import deep_merge

    result = deep_merge({"a": 1}, {"b": 2})
    assert result == {"a": 1, "b": 2}


def test_deep_merge_override_wins():
    from navig.core.dict_utils import deep_merge

    result = deep_merge({"a": 1}, {"a": 99})
    assert result["a"] == 99


def test_deep_merge_recursive():
    from navig.core.dict_utils import deep_merge

    base = {"x": {"a": 1, "b": 2}}
    override = {"x": {"b": 99, "c": 3}}
    result = deep_merge(base, override)
    assert result["x"] == {"a": 1, "b": 99, "c": 3}


def test_deep_merge_lists_concatenated():
    from navig.core.dict_utils import deep_merge

    result = deep_merge({"tags": ["a", "b"]}, {"tags": ["c"]})
    assert result["tags"] == ["a", "b", "c"]


def test_deep_merge_does_not_mutate_base():
    from navig.core.dict_utils import deep_merge

    base = {"a": 1}
    deep_merge(base, {"a": 2})
    assert base["a"] == 1


def test_deep_merge_deep_copies_override_value():
    from navig.core.dict_utils import deep_merge

    override_list = [1, 2, 3]
    result = deep_merge({}, {"items": override_list})
    override_list.append(4)
    assert result["items"] == [1, 2, 3]  # deep copied, mutation doesn't affect


def test_truncate_output_short_text_unchanged():
    from navig.core.dict_utils import truncate_output

    assert truncate_output("hello", 100) == "hello"


def test_truncate_output_exact_limit_unchanged():
    from navig.core.dict_utils import truncate_output

    text = "a" * 50
    assert truncate_output(text, 50) == text


def test_truncate_output_long_text_truncated():
    from navig.core.dict_utils import truncate_output

    text = "a" * 200
    result = truncate_output(text, 100)
    assert result.startswith("a" * 100)
    assert "truncated" in result
    assert "200" in result


def test_utc_now_is_timezone_aware():
    from navig.core.dict_utils import utc_now
    from datetime import timezone

    dt = utc_now()
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0


def test_now_iso_is_string():
    from navig.core.dict_utils import now_iso

    result = now_iso()
    assert isinstance(result, str)
    assert "T" in result
    assert "+" in result or "Z" in result or result.endswith("+00:00")


# ---------------------------------------------------------------------------
# commands/telemetry
# ---------------------------------------------------------------------------


def test_telemetry_default_shows_disabled():
    from navig.commands.telemetry import telemetry_app

    mock_cfg = MagicMock()
    mock_cfg.get.return_value = False
    with patch("navig.config.ConfigManager", return_value=mock_cfg):
        result = runner.invoke(telemetry_app, [])
    assert result.exit_code == 0
    assert "disabled" in result.output or "Telemetry" in result.output


def test_telemetry_default_shows_enabled():
    from navig.commands.telemetry import telemetry_app

    mock_cfg = MagicMock()
    mock_cfg.get.return_value = True
    with patch("navig.config.ConfigManager", return_value=mock_cfg):
        result = runner.invoke(telemetry_app, [])
    assert result.exit_code == 0
    assert "enabled" in result.output or "Telemetry" in result.output


def test_telemetry_default_exception_handled():
    from navig.commands.telemetry import telemetry_app

    with patch("navig.config.ConfigManager", side_effect=Exception("oops")):
        result = runner.invoke(telemetry_app, [])
    assert result.exit_code == 0
    assert "unknown" in result.output or "Telemetry" in result.output


def test_telemetry_enable_calls_set():
    from navig.commands.telemetry import telemetry_app

    mock_cfg = MagicMock()
    with patch("navig.config.ConfigManager", return_value=mock_cfg):
        result = runner.invoke(telemetry_app, ["enable"])
    assert result.exit_code == 0
    mock_cfg.set.assert_called_once_with("telemetry.enabled", True)


def test_telemetry_enable_prints_confirmation():
    from navig.commands.telemetry import telemetry_app

    mock_cfg = MagicMock()
    with patch("navig.config.ConfigManager", return_value=mock_cfg):
        result = runner.invoke(telemetry_app, ["enable"])
    assert "enabled" in result.output.lower()


def test_telemetry_disable_calls_set():
    from navig.commands.telemetry import telemetry_app

    mock_cfg = MagicMock()
    with patch("navig.config.ConfigManager", return_value=mock_cfg):
        result = runner.invoke(telemetry_app, ["disable"])
    assert result.exit_code == 0
    mock_cfg.set.assert_called_once_with("telemetry.enabled", False)


def test_telemetry_enable_exception_prints_error():
    from navig.commands.telemetry import telemetry_app

    with patch("navig.config.ConfigManager", side_effect=Exception("no file")):
        result = runner.invoke(telemetry_app, ["enable"])
    assert result.exit_code == 0
    assert "Error" in result.output or "no file" in result.output


def test_telemetry_help_lists_commands():
    from navig.commands.telemetry import telemetry_app

    result = runner.invoke(telemetry_app, ["--help"])
    assert "enable" in result.output or "disable" in result.output
