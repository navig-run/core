"""
Tests for navig._daemon_defaults constants and
navig.proactive_assistant.ProactiveAssistant._load_assistant_config logic.

No I/O or network; ConfigManager is mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig._daemon_defaults import _DAEMON_PORT, _OAUTH_REDIRECT_PORT


# ---------------------------------------------------------------------------
# _daemon_defaults constants
# ---------------------------------------------------------------------------


class TestDaemonDefaults:
    def test_daemon_port_is_int(self):
        assert isinstance(_DAEMON_PORT, int)

    def test_daemon_port_is_positive(self):
        assert _DAEMON_PORT > 0

    def test_daemon_port_is_ephemeral_or_registered(self):
        # Valid TCP ports are 1–65535
        assert 1 <= _DAEMON_PORT <= 65535

    def test_oauth_redirect_port_is_int(self):
        assert isinstance(_OAUTH_REDIRECT_PORT, int)

    def test_oauth_redirect_port_is_positive(self):
        assert _OAUTH_REDIRECT_PORT > 0

    def test_oauth_redirect_port_is_valid_tcp(self):
        assert 1 <= _OAUTH_REDIRECT_PORT <= 65535

    def test_ports_are_different(self):
        assert _DAEMON_PORT != _OAUTH_REDIRECT_PORT


# ---------------------------------------------------------------------------
# ProactiveAssistant._load_assistant_config
# ---------------------------------------------------------------------------


def _make_assistant(global_config: dict | None = None):
    """Create a ProactiveAssistant backed by a mock ConfigManager."""
    config_manager = MagicMock()
    config_manager.global_config = global_config or {}

    from navig.proactive_assistant import ProactiveAssistant

    with patch("navig.proactive_assistant.ensure_navig_directory") as mock_dir:
        mock_dir.return_value = MagicMock()
        assistant = ProactiveAssistant(config_manager)

    return assistant


class TestLoadAssistantConfig:
    def test_returns_dict(self):
        assistant = _make_assistant()
        assert isinstance(assistant.assistant_config, dict)

    def test_default_enabled_is_true(self):
        assistant = _make_assistant()
        assert assistant.assistant_config["enabled"] is True

    def test_default_suggestion_level(self):
        assistant = _make_assistant()
        assert assistant.assistant_config["suggestion_level"] == "normal"

    def test_default_has_thresholds(self):
        assistant = _make_assistant()
        thresholds = assistant.assistant_config["thresholds"]
        assert "cpu_warning" in thresholds
        assert "disk_critical" in thresholds

    def test_default_cpu_warning_sensible(self):
        assistant = _make_assistant()
        cpu_warn = assistant.assistant_config["thresholds"]["cpu_warning"]
        assert 0 < cpu_warn < 100

    def test_user_config_overrides_enabled(self):
        assistant = _make_assistant(
            global_config={"proactive_assistant": {"enabled": False}}
        )
        assert assistant.assistant_config["enabled"] is False

    def test_user_config_overrides_suggestion_level(self):
        assistant = _make_assistant(
            global_config={"proactive_assistant": {"suggestion_level": "minimal"}}
        )
        assert assistant.assistant_config["suggestion_level"] == "minimal"

    def test_user_dict_config_deep_merged_with_thresholds(self):
        """User can override a single threshold without wiping the rest."""
        assistant = _make_assistant(
            global_config={"proactive_assistant": {"thresholds": {"cpu_warning": 70}}}
        )
        thresholds = assistant.assistant_config["thresholds"]
        assert thresholds["cpu_warning"] == 70
        # Other threshold keys should still be present from defaults
        assert "disk_critical" in thresholds

    def test_no_proactive_config_uses_defaults(self):
        """Completely absent proactive_assistant key → pure defaults."""
        assistant = _make_assistant(global_config={"other_key": "value"})
        assert assistant.assistant_config["auto_analysis"] is True

    def test_default_log_paths_present(self):
        assistant = _make_assistant()
        assert "log_paths" in assistant.assistant_config
        assert "nginx" in assistant.assistant_config["log_paths"]

    def test_default_max_history_entries_positive(self):
        assistant = _make_assistant()
        assert assistant.assistant_config["max_history_entries"] > 0
