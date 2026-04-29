"""
Batch 87 — navig/core/execution.py
Tests for ExecutionSettings: get_mode, set_mode, get_confirmation_level, set_confirmation_level, get_settings.
"""
from unittest.mock import MagicMock

import pytest

from navig.core.execution import (
    VALID_CONFIRMATION_LEVELS,
    VALID_MODES,
    ExecutionSettings,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_valid_modes_contains_interactive(self):
        assert "interactive" in VALID_MODES

    def test_valid_modes_contains_auto(self):
        assert "auto" in VALID_MODES

    def test_valid_confirmation_levels(self):
        assert set(VALID_CONFIRMATION_LEVELS) == {"critical", "standard", "verbose"}


# ---------------------------------------------------------------------------
# Helper: build a minimal mock provider
# ---------------------------------------------------------------------------


def _make_provider(global_config: dict | None = None) -> MagicMock:
    provider = MagicMock()
    provider.global_config = global_config or {}
    provider._save_global_config = MagicMock()
    return provider


# ---------------------------------------------------------------------------
# ExecutionSettings.get_mode
# ---------------------------------------------------------------------------


class TestGetMode:
    def test_default_is_interactive(self, monkeypatch, tmp_path):
        # Ensure no .navig/config.yaml in cwd
        monkeypatch.chdir(tmp_path)
        settings = ExecutionSettings(_make_provider())
        assert settings.get_mode() == "interactive"

    def test_global_config_mode_used(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        settings = ExecutionSettings(_make_provider({"execution": {"mode": "auto"}}))
        assert settings.get_mode() == "auto"

    def test_local_config_overrides_global(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Create .navig/config.yaml with mode = auto
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        (navig_dir / "config.yaml").write_text("execution:\n  mode: auto\n")
        # Global says interactive
        settings = ExecutionSettings(_make_provider({"execution": {"mode": "interactive"}}))
        assert settings.get_mode() == "auto"

    def test_no_execution_key_defaults_interactive(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        settings = ExecutionSettings(_make_provider({"other": "stuff"}))
        assert settings.get_mode() == "interactive"


# ---------------------------------------------------------------------------
# ExecutionSettings.set_mode
# ---------------------------------------------------------------------------


class TestSetMode:
    def test_set_valid_mode(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = _make_provider()
        settings = ExecutionSettings(provider)
        settings.set_mode("auto")
        provider._save_global_config.assert_called_once()
        saved = provider._save_global_config.call_args[0][0]
        assert saved["execution"]["mode"] == "auto"

    def test_set_mode_invalid_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        settings = ExecutionSettings(_make_provider())
        with pytest.raises(ValueError, match="interactive"):
            settings.set_mode("turbo")

    def test_set_mode_creates_execution_key(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = _make_provider({})  # no execution key
        settings = ExecutionSettings(provider)
        settings.set_mode("interactive")
        saved = provider._save_global_config.call_args[0][0]
        assert "execution" in saved

    def test_set_mode_interactive_valid(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = _make_provider()
        settings = ExecutionSettings(provider)
        settings.set_mode("interactive")  # should not raise


# ---------------------------------------------------------------------------
# ExecutionSettings.get_confirmation_level
# ---------------------------------------------------------------------------


class TestGetConfirmationLevel:
    def test_default_is_standard(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        settings = ExecutionSettings(_make_provider())
        assert settings.get_confirmation_level() == "standard"

    def test_global_config_level_used(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        settings = ExecutionSettings(_make_provider({"execution": {"confirmation_level": "critical"}}))
        assert settings.get_confirmation_level() == "critical"

    def test_local_config_overrides_global_level(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        (navig_dir / "config.yaml").write_text("execution:\n  confirmation_level: verbose\n")
        settings = ExecutionSettings(_make_provider({"execution": {"confirmation_level": "critical"}}))
        assert settings.get_confirmation_level() == "verbose"


# ---------------------------------------------------------------------------
# ExecutionSettings.set_confirmation_level
# ---------------------------------------------------------------------------


class TestSetConfirmationLevel:
    def test_set_valid_level(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        provider = _make_provider()
        settings = ExecutionSettings(provider)
        settings.set_confirmation_level("critical")
        saved = provider._save_global_config.call_args[0][0]
        assert saved["execution"]["confirmation_level"] == "critical"

    def test_set_invalid_level_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        settings = ExecutionSettings(_make_provider())
        with pytest.raises(ValueError):
            settings.set_confirmation_level("never")

    def test_all_valid_levels_accepted(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        for level in VALID_CONFIRMATION_LEVELS:
            provider = _make_provider()
            settings = ExecutionSettings(provider)
            settings.set_confirmation_level(level)  # should not raise


# ---------------------------------------------------------------------------
# ExecutionSettings.get_settings
# ---------------------------------------------------------------------------


class TestGetSettings:
    def test_returns_dict_with_mode_and_level(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        settings = ExecutionSettings(_make_provider())
        result = settings.get_settings()
        assert "mode" in result
        assert "confirmation_level" in result

    def test_defaults_in_get_settings(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        settings = ExecutionSettings(_make_provider())
        result = settings.get_settings()
        assert result["mode"] == "interactive"
        assert result["confirmation_level"] == "standard"

    def test_custom_values_in_get_settings(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        settings = ExecutionSettings(_make_provider({"execution": {"mode": "auto", "confirmation_level": "verbose"}}))
        result = settings.get_settings()
        assert result["mode"] == "auto"
        assert result["confirmation_level"] == "verbose"
