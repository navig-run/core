"""Tests for navig/core/execution.py and navig/core/model_routing.py — batch 87."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# ExecutionSettings
# ---------------------------------------------------------------------------
from navig.core.execution import (
    VALID_CONFIRMATION_LEVELS,
    VALID_MODES,
    ExecutionSettings,
)


def _make_provider(global_config=None):
    provider = MagicMock()
    provider.global_config = global_config or {}
    provider._save_global_config = MagicMock()
    return provider


class TestExecutionSettingsConstants:
    def test_valid_modes_contains_expected(self):
        assert "interactive" in VALID_MODES
        assert "auto" in VALID_MODES

    def test_valid_confirmation_levels(self):
        assert "critical" in VALID_CONFIRMATION_LEVELS
        assert "standard" in VALID_CONFIRMATION_LEVELS
        assert "verbose" in VALID_CONFIRMATION_LEVELS


class TestExecutionSettingsGetMode:
    def test_default_mode_is_interactive(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # ensure no .navig/config.yaml
        provider = _make_provider()
        es = ExecutionSettings(provider)
        assert es.get_mode() == "interactive"

    def test_global_config_mode_used(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = _make_provider({"execution": {"mode": "auto"}})
        es = ExecutionSettings(provider)
        assert es.get_mode() == "auto"

    def test_local_config_overrides_global(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        local_dir = tmp_path / ".navig"
        local_dir.mkdir()
        (local_dir / "config.yaml").write_text("execution:\n  mode: auto\n")
        provider = _make_provider({"execution": {"mode": "interactive"}})
        es = ExecutionSettings(provider)
        assert es.get_mode() == "auto"


class TestExecutionSettingsSetMode:
    def test_set_valid_mode(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = _make_provider()
        es = ExecutionSettings(provider)
        es.set_mode("auto")
        provider._save_global_config.assert_called_once()
        saved = provider._save_global_config.call_args[0][0]
        assert saved["execution"]["mode"] == "auto"

    def test_set_invalid_mode_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = _make_provider()
        es = ExecutionSettings(provider)
        with pytest.raises(ValueError, match="Invalid mode"):
            es.set_mode("turbo")

    def test_set_mode_creates_execution_key(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = _make_provider({})
        es = ExecutionSettings(provider)
        es.set_mode("interactive")
        saved = provider._save_global_config.call_args[0][0]
        assert "execution" in saved


class TestExecutionSettingsGetConfirmationLevel:
    def test_default_level_is_standard(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = _make_provider()
        es = ExecutionSettings(provider)
        assert es.get_confirmation_level() == "standard"

    def test_global_config_level_used(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = _make_provider({"execution": {"confirmation_level": "critical"}})
        es = ExecutionSettings(provider)
        assert es.get_confirmation_level() == "critical"

    def test_local_config_overrides_global_level(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        local_dir = tmp_path / ".navig"
        local_dir.mkdir()
        (local_dir / "config.yaml").write_text("execution:\n  confirmation_level: verbose\n")
        provider = _make_provider({"execution": {"confirmation_level": "critical"}})
        es = ExecutionSettings(provider)
        assert es.get_confirmation_level() == "verbose"


class TestExecutionSettingsSetConfirmationLevel:
    def test_set_valid_level(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = _make_provider()
        es = ExecutionSettings(provider)
        es.set_confirmation_level("verbose")
        saved = provider._save_global_config.call_args[0][0]
        assert saved["execution"]["confirmation_level"] == "verbose"

    def test_set_invalid_level_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = _make_provider()
        es = ExecutionSettings(provider)
        with pytest.raises(ValueError, match="Invalid level"):
            es.set_confirmation_level("extreme")


class TestExecutionSettingsGetSettings:
    def test_returns_both_keys(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = _make_provider()
        es = ExecutionSettings(provider)
        result = es.get_settings()
        assert "mode" in result
        assert "confirmation_level" in result

    def test_returns_correct_values(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = _make_provider({"execution": {"mode": "auto", "confirmation_level": "critical"}})
        es = ExecutionSettings(provider)
        result = es.get_settings()
        assert result["mode"] == "auto"
        assert result["confirmation_level"] == "critical"


class TestExecutionSettingsLocalConfigCache:
    def test_cache_invalidated_on_mtime_change(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        local_dir = tmp_path / ".navig"
        local_dir.mkdir()
        cfg_file = local_dir / "config.yaml"
        cfg_file.write_text("execution:\n  mode: auto\n")

        provider = _make_provider()
        es = ExecutionSettings(provider)
        assert es.get_mode() == "auto"

        # Update file content
        cfg_file.write_text("execution:\n  mode: interactive\n")
        # Touch file to change mtime (ensure different mtime)
        import time
        time.sleep(0.01)
        cfg_file.touch()

        assert es.get_mode() == "interactive"


# ---------------------------------------------------------------------------
# model_routing
# ---------------------------------------------------------------------------
from navig.core.model_routing import (
    _coerce_bool,
    _coerce_int,
    _DEFAULT_MAX_SIMPLE_CHARS,
    _DEFAULT_MAX_SIMPLE_WORDS,
    choose_cheap_model_route,
    get_routing_config,
    is_simple_turn,
)


class TestCoerceBool:
    def test_true_bool(self):
        assert _coerce_bool(True) is True

    def test_false_bool(self):
        assert _coerce_bool(False) is False

    def test_truthy_string(self):
        assert _coerce_bool("yes") is True
        assert _coerce_bool("true") is True
        assert _coerce_bool("1") is True

    def test_falsy_string(self):
        assert _coerce_bool("false") is False
        assert _coerce_bool("0") is False
        assert _coerce_bool("no") is False
        assert _coerce_bool("") is False

    def test_int_nonzero(self):
        assert _coerce_bool(1) is True

    def test_int_zero(self):
        assert _coerce_bool(0) is False

    def test_none_returns_default(self):
        assert _coerce_bool(None, default=True) is True
        assert _coerce_bool(None, default=False) is False


class TestCoerceInt:
    def test_int_value(self):
        assert _coerce_int(42, default=0) == 42

    def test_string_int(self):
        assert _coerce_int("100", default=0) == 100

    def test_invalid_returns_default(self):
        assert _coerce_int("abc", default=99) == 99
        assert _coerce_int(None, default=5) == 5


class TestIsSimpleTurn:
    def test_empty_returns_false(self):
        assert is_simple_turn("") is False
        assert is_simple_turn("   ") is False

    def test_short_greeting_is_simple(self):
        assert is_simple_turn("Hello, how are you?") is True

    def test_too_long_chars_is_not_simple(self):
        long_msg = "a " * 100  # 200 chars
        assert is_simple_turn(long_msg) is False

    def test_too_many_words_is_not_simple(self):
        many_words = " ".join(["word"] * 30)
        assert is_simple_turn(many_words) is False

    def test_code_block_is_not_simple(self):
        assert is_simple_turn("```python\nprint('hi')\n```") is False

    def test_inline_code_is_not_simple(self):
        assert is_simple_turn("Use `print()` to debug") is False

    def test_url_is_not_simple(self):
        assert is_simple_turn("Check https://example.com") is False

    def test_multiline_is_not_simple(self):
        assert is_simple_turn("line1\nline2\nline3") is False

    def test_complex_keyword_is_not_simple(self):
        assert is_simple_turn("Can you debug this error?") is False
        assert is_simple_turn("Implement a new feature") is False
        assert is_simple_turn("Help me with docker") is False

    def test_custom_limits_respected(self):
        msg = "Hello"  # 5 chars, 1 word
        assert is_simple_turn(msg, max_chars=4, max_words=28) is False
        assert is_simple_turn(msg, max_chars=10, max_words=0) is False


class TestChooseCheapModelRoute:
    def _routing_cfg(self, **kwargs):
        return {
            "enabled": True,
            "cheap_model": {"provider": "deepseek", "model": "deepseek-chat"},
            **kwargs,
        }

    def test_returns_none_when_disabled(self):
        cfg = self._routing_cfg(enabled=False)
        result = choose_cheap_model_route("Hi", cfg)
        assert result is None

    def test_returns_none_for_none_config(self):
        result = choose_cheap_model_route("Hi", None)
        assert result is None

    def test_returns_none_for_complex_message(self):
        cfg = self._routing_cfg()
        result = choose_cheap_model_route("Please debug this traceback in my code", cfg)
        assert result is None

    def test_returns_route_for_simple_message(self):
        cfg = self._routing_cfg()
        result = choose_cheap_model_route("What time is it?", cfg)
        assert result is not None
        assert result["provider"] == "deepseek"
        assert result["model"] == "deepseek-chat"
        assert result["routing_reason"] == "simple_turn"

    def test_returns_none_when_provider_missing(self):
        cfg = {"enabled": True, "cheap_model": {"model": "gpt-3.5"}}
        result = choose_cheap_model_route("Hi", cfg)
        assert result is None

    def test_returns_none_when_model_missing(self):
        cfg = {"enabled": True, "cheap_model": {"provider": "openai"}}
        result = choose_cheap_model_route("Hi", cfg)
        assert result is None

    def test_route_includes_all_cheap_model_keys(self):
        cfg = self._routing_cfg()
        cfg["cheap_model"]["temperature"] = 0.3
        result = choose_cheap_model_route("Still here?", cfg)
        assert result is not None
        assert result.get("temperature") == 0.3

    def test_custom_char_limit_applied(self):
        cfg = self._routing_cfg(max_simple_chars=5)
        result = choose_cheap_model_route("Hello there friend", cfg)
        assert result is None  # exceeds 5 chars


class TestGetRoutingConfig:
    def test_returns_none_on_import_error(self):
        with patch("navig.config.get_config_manager", side_effect=Exception("unavail")):
            result = get_routing_config()
        assert result is None

    def test_returns_none_when_config_not_dict(self):
        mock_cfg = MagicMock()
        mock_cfg.get.return_value = "not-a-dict"
        with patch("navig.config.get_config_manager", return_value=mock_cfg):
            result = get_routing_config()
        assert result is None

    def test_returns_dict_when_valid(self):
        mock_cfg = MagicMock()
        mock_cfg.get.return_value = {"enabled": True}
        with patch("navig.config.get_config_manager", return_value=mock_cfg):
            result = get_routing_config()
        assert result == {"enabled": True}
