"""Tests for navig.deprecation"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from navig.deprecation import (
    DEPRECATION_MAP,
    deprecated_command,
    deprecation_warning,
    get_canonical_command,
)


class TestDeprecatedCommandDecorator:
    def test_calls_wrapped_function(self):
        mock_fn = MagicMock(return_value="result")
        wrapped = deprecated_command("navig old", "navig new")(mock_fn)
        with patch("navig.deprecation.ch") as mock_ch:
            result = wrapped("arg1", key="val")
        mock_fn.assert_called_once_with("arg1", key="val")
        assert result == "result"

    def test_shows_warning_by_default(self):
        mock_fn = MagicMock()
        wrapped = deprecated_command("navig old", "navig new")(mock_fn)
        with patch("navig.deprecation.ch") as mock_ch:
            wrapped()
        mock_ch.warning.assert_called_once()
        warning_args = mock_ch.warning.call_args[0]
        assert "navig old" in warning_args[0]
        assert "navig new" in warning_args[1]

    def test_warning_contains_version(self):
        mock_fn = MagicMock()
        wrapped = deprecated_command("navig old", "navig new", version_removed="4.0.0")(mock_fn)
        with patch("navig.deprecation.ch") as mock_ch:
            wrapped()
        msg = mock_ch.warning.call_args[0][0]
        assert "4.0.0" in msg

    def test_show_warning_false_suppresses_warning(self):
        mock_fn = MagicMock()
        wrapped = deprecated_command("navig old", "navig new", show_warning=False)(mock_fn)
        with patch("navig.deprecation.ch") as mock_ch:
            wrapped()
        mock_ch.warning.assert_not_called()

    def test_preserves_function_name(self):
        def my_command():
            pass

        wrapped = deprecated_command("navig old", "navig new")(my_command)
        assert wrapped.__name__ == "my_command"

    def test_preserves_docstring(self):
        def my_command():
            """Original docstring."""

        wrapped = deprecated_command("navig old", "navig new")(my_command)
        assert wrapped.__doc__ == "Original docstring."

    def test_returns_function_return_value(self):
        def my_command(x, y):
            return x + y

        wrapped = deprecated_command("navig old", "navig new")(my_command)
        with patch("navig.deprecation.ch"):
            assert wrapped(2, 3) == 5


class TestDeprecationWarning:
    def test_calls_ch_warning(self):
        with patch("navig.deprecation.ch") as mock_ch:
            deprecation_warning("navig old-cmd", "navig new-cmd")
        mock_ch.warning.assert_called_once()

    def test_includes_old_command_in_message(self):
        with patch("navig.deprecation.ch") as mock_ch:
            deprecation_warning("navig old-cmd", "navig new-cmd")
        msg = mock_ch.warning.call_args[0][0]
        assert "navig old-cmd" in msg

    def test_includes_new_command_in_hint(self):
        with patch("navig.deprecation.ch") as mock_ch:
            deprecation_warning("navig old-cmd", "navig new-cmd")
        hint = mock_ch.warning.call_args[0][1]
        assert "navig new-cmd" in hint

    def test_custom_version_in_message(self):
        with patch("navig.deprecation.ch") as mock_ch:
            deprecation_warning("old", "new", version="5.0.0")
        msg = mock_ch.warning.call_args[0][0]
        assert "5.0.0" in msg


class TestDeprecationMap:
    def test_map_is_non_empty(self):
        assert len(DEPRECATION_MAP) > 0

    def test_all_values_are_strings(self):
        for k, v in DEPRECATION_MAP.items():
            assert isinstance(k, str), f"Key {k!r} is not a string"
            assert isinstance(v, str), f"Value {v!r} for key {k!r} is not a string"

    def test_known_host_mapping(self):
        assert DEPRECATION_MAP.get("navig host info") == "navig host show"

    def test_known_file_mapping(self):
        assert DEPRECATION_MAP.get("navig upload") == "navig file add"

    def test_known_db_mapping(self):
        assert DEPRECATION_MAP.get("navig sql") == "navig db run"


class TestGetCanonicalCommand:
    def test_known_deprecated_returns_canonical(self):
        result = get_canonical_command("navig host info")
        assert result == "navig host show"

    def test_unknown_command_returns_none(self):
        result = get_canonical_command("navig nonexistent command xyz")
        assert result is None

    def test_file_commands_resolved(self):
        assert get_canonical_command("navig upload") == "navig file add"
        assert get_canonical_command("navig cat") == "navig file show"
        assert get_canonical_command("navig ls") == "navig file list"

    def test_vault_alias_resolved(self):
        assert get_canonical_command("navig cred") == "navig vault"
