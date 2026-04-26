"""Tests for navig.cli.selector — CommandEntry, fzf_or_fallback, _numbered_prompt."""
from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from navig.cli.selector import CommandEntry, _hint, _numbered_prompt, fzf_or_fallback


# ── helpers ──────────────────────────────────────────────────


def _entry(name: str = "list", desc: str = "List items", domain: str = "host") -> CommandEntry:
    return CommandEntry(name=name, description=desc, domain=domain)


def _entries(*names: str) -> list[CommandEntry]:
    return [_entry(name=n, desc=f"Desc {n}") for n in names]


# ── CommandEntry ──────────────────────────────────────────────


class TestCommandEntry:
    def test_fields(self):
        e = CommandEntry(name="list", description="List things", domain="host")
        assert e.name == "list"
        assert e.description == "List things"
        assert e.domain == "host"

    def test_domain_defaults_empty(self):
        e = CommandEntry(name="list", description="")
        assert e.domain == ""


# ── _hint ─────────────────────────────────────────────────────


class TestHint:
    def setup_method(self):
        # Clear the module-level _hints_shown set before each test
        import navig.cli.selector as sel_mod
        sel_mod._hints_shown.clear()

    def test_hint_emitted_first_time(self, capsys):
        with patch.object(sys.stderr, "isatty", return_value=True):
            _hint("test-key", "my hint message")
        # No easy capsys for stderr; just check no exception
        import navig.cli.selector as sel_mod
        assert "test-key" in sel_mod._hints_shown

    def test_hint_not_repeated(self, capsys):
        import navig.cli.selector as sel_mod
        sel_mod._hints_shown.add("repeat-key")
        output = StringIO()
        with patch("sys.stderr", output):
            _hint("repeat-key", "should not emit")
        assert output.getvalue() == ""

    def test_hint_not_emitted_non_tty(self):
        import navig.cli.selector as sel_mod
        sel_mod._hints_shown.clear()
        output = StringIO()
        with patch("sys.stderr", output):
            with patch.object(output, "isatty", return_value=False):
                _hint("new-key", "should not emit")


# ── fzf_or_fallback ───────────────────────────────────────────


class TestFzfOrFallback:
    def test_empty_commands_returns_none(self):
        result = fzf_or_fallback([])
        assert result is None

    def test_fzf_not_available_falls_to_arrow_selector(self):
        entries = _entries("list", "add")
        with patch("navig.cli.selector.shutil.which", return_value=None):
            with patch("navig.cli.selector._arrow_selector", return_value=entries[0]) as mock_arrow:
                result = fzf_or_fallback(entries)
        mock_arrow.assert_called_once()
        assert result is entries[0]

    def test_fzf_available_tty_user_selects(self):
        entries = _entries("list", "add")
        mock_proc = MagicMock(returncode=0, stdout="list                       Desc list\n")
        with patch("navig.cli.selector.shutil.which", return_value="/usr/bin/fzf"):
            with patch.object(sys.stdin, "isatty", return_value=True):
                with patch("navig.cli.selector.subprocess.run", return_value=mock_proc):
                    result = fzf_or_fallback(entries)
        assert result is not None
        assert result.name == "list"

    def test_fzf_returncode_nonzero_returns_none(self):
        entries = _entries("list")
        mock_proc = MagicMock(returncode=130, stdout="")
        with patch("navig.cli.selector.shutil.which", return_value="/usr/bin/fzf"):
            with patch.object(sys.stdin, "isatty", return_value=True):
                with patch("navig.cli.selector.subprocess.run", return_value=mock_proc):
                    result = fzf_or_fallback(entries)
        assert result is None

    def test_fzf_exception_falls_to_arrow_selector(self):
        entries = _entries("list")
        with patch("navig.cli.selector.shutil.which", return_value="/usr/bin/fzf"):
            with patch.object(sys.stdin, "isatty", return_value=True):
                with patch("navig.cli.selector.subprocess.run", side_effect=OSError("fzf broke")):
                    with patch("navig.cli.selector._arrow_selector", return_value=None) as mock_arrow:
                        result = fzf_or_fallback(entries)
        mock_arrow.assert_called_once()


# ── _numbered_prompt ──────────────────────────────────────────


class TestNumberedPrompt:
    def test_valid_number_selection(self):
        entries = _entries("list", "add", "remove")
        with patch("builtins.input", return_value="2"):
            result = _numbered_prompt(entries, "> ")
        assert result is entries[1]

    def test_selection_1(self):
        entries = _entries("list", "add")
        with patch("builtins.input", return_value="1"):
            result = _numbered_prompt(entries, "> ")
        assert result is entries[0]

    def test_out_of_range_returns_none(self):
        entries = _entries("list", "add")
        with patch("builtins.input", return_value="99"):
            result = _numbered_prompt(entries, "> ")
        assert result is None

    def test_non_numeric_returns_none(self):
        entries = _entries("list")
        with patch("builtins.input", return_value="abc"):
            result = _numbered_prompt(entries, "> ")
        assert result is None

    def test_empty_input_returns_none(self):
        entries = _entries("list")
        with patch("builtins.input", return_value=""):
            result = _numbered_prompt(entries, "> ")
        assert result is None

    def test_eof_error_returns_none(self):
        entries = _entries("list")
        with patch("builtins.input", side_effect=EOFError):
            result = _numbered_prompt(entries, "> ")
        assert result is None

    def test_keyboard_interrupt_returns_none(self):
        entries = _entries("list")
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            result = _numbered_prompt(entries, "> ")
        assert result is None

    def test_zero_index_returns_none(self):
        entries = _entries("list")
        with patch("builtins.input", return_value="0"):
            result = _numbered_prompt(entries, "> ")
        assert result is None

    def test_negative_number_string_returns_none(self):
        entries = _entries("list")
        with patch("builtins.input", return_value="-1"):
            result = _numbered_prompt(entries, "> ")
        assert result is None
