"""Tests for navig.ui.prompts — render_action_approval with input mocking."""

from __future__ import annotations

from unittest.mock import patch

from navig.ui.prompts import render_action_approval


class TestRenderActionApproval:
    def test_returns_true_on_y(self):
        with patch("builtins.input", return_value="y"):
            assert render_action_approval("navig run ls") is True

    def test_returns_true_on_yes(self):
        with patch("builtins.input", return_value="yes"):
            assert render_action_approval("navig run ls") is True

    def test_returns_true_case_insensitive(self):
        with patch("builtins.input", return_value="Y"):
            assert render_action_approval("cmd") is True

    def test_returns_false_on_n(self):
        with patch("builtins.input", return_value="n"):
            assert render_action_approval("navig run ls") is False

    def test_returns_false_on_empty(self):
        with patch("builtins.input", return_value=""):
            assert render_action_approval("cmd") is False

    def test_returns_false_on_eof(self):
        with patch("builtins.input", side_effect=EOFError):
            assert render_action_approval("cmd") is False

    def test_returns_false_on_keyboard_interrupt(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            assert render_action_approval("cmd") is False

    def test_accepts_hint_parameter(self):
        with patch("builtins.input", return_value="y"):
            result = render_action_approval("cmd", hint="This will restart nginx")
        assert result is True

    def test_accepts_custom_prompt(self):
        with patch("builtins.input", return_value="no"):
            result = render_action_approval("cmd", prompt="Are you sure?")
        assert result is False
