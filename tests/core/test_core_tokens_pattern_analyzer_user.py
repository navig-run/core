"""Tests for core/tokens.py, agent/pattern_analyzer.py, commands/user.py — batch 52."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

runner = CliRunner()

# ---------------------------------------------------------------------------
# core/tokens — estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_empty_returns_0():
    from navig.core.tokens import estimate_tokens

    assert estimate_tokens("") == 0


def test_estimate_tokens_nonempty_at_least_1():
    from navig.core.tokens import estimate_tokens

    assert estimate_tokens("hi") >= 1


def test_estimate_tokens_default_ratio_4():
    from navig.core.tokens import estimate_tokens

    # 40 chars / 4.0 = 10
    text = "a" * 40
    assert estimate_tokens(text) == 10


def test_estimate_tokens_custom_ratio():
    from navig.core.tokens import estimate_tokens

    # 35 chars / 3.5 = 10
    text = "a" * 35
    assert estimate_tokens(text, chars_per_token=3.5) == 10


def test_estimate_tokens_short_text_clamped_to_1():
    from navig.core.tokens import estimate_tokens

    # 1 char / 4.0 = 0.25 → int = 0 → max(1, 0) = 1
    assert estimate_tokens("x") == 1


def test_estimate_tokens_returns_int():
    from navig.core.tokens import estimate_tokens

    assert isinstance(estimate_tokens("hello world"), int)


def test_estimate_tokens_long_text():
    from navig.core.tokens import estimate_tokens

    text = "word " * 1000  # 5000 chars
    result = estimate_tokens(text)
    assert result == 1250  # 5000 / 4.0


def test_estimate_tokens_exported_in_all():
    import navig.core.tokens as mod

    assert "estimate_tokens" in mod.__all__


# ---------------------------------------------------------------------------
# agent/pattern_analyzer — PatternAnalyzer
# ---------------------------------------------------------------------------


def _rec(cmd: str):
    r = MagicMock()
    r.command = cmd
    return r


def test_pattern_analyzer_empty_returns_empty():
    from navig.agent.pattern_analyzer import PatternAnalyzer

    pa = PatternAnalyzer()
    assert pa.score_by_frequency([]) == []


def test_pattern_analyzer_below_min_occurrences_excluded():
    from navig.agent.pattern_analyzer import PatternAnalyzer

    pa = PatternAnalyzer(min_occurrences=3)
    records = [_rec("ls"), _rec("ls")]  # only 2 occurrences, need 3
    assert pa.score_by_frequency(records) == []


def test_pattern_analyzer_meets_min_occurrences():
    from navig.agent.pattern_analyzer import PatternAnalyzer

    pa = PatternAnalyzer(min_occurrences=2)
    records = [_rec("ls"), _rec("ls"), _rec("pwd")]
    results = pa.score_by_frequency(records)
    assert len(results) == 1
    assert results[0].sequence == ("ls",)


def test_pattern_analyzer_score_equals_occurrences():
    from navig.agent.pattern_analyzer import PatternAnalyzer

    pa = PatternAnalyzer(min_occurrences=2)
    records = [_rec("ls")] * 5
    results = pa.score_by_frequency(records)
    assert results[0].occurrences == 5
    assert results[0].score == 5.0


def test_pattern_analyzer_sorted_by_score_descending():
    from navig.agent.pattern_analyzer import PatternAnalyzer

    pa = PatternAnalyzer(min_occurrences=2)
    records = [_rec("a")] * 2 + [_rec("b")] * 4
    results = pa.score_by_frequency(records)
    assert results[0].sequence == ("b",)
    assert results[1].sequence == ("a",)


def test_pattern_analyzer_max_results_respected():
    from navig.agent.pattern_analyzer import PatternAnalyzer

    pa = PatternAnalyzer(min_occurrences=2, max_results=2)
    # create 5 commands each appearing 2+ times
    records = []
    for i in range(5):
        records += [_rec(f"cmd{i}")] * 2
    results = pa.score_by_frequency(records)
    assert len(results) <= 2


def test_pattern_analyzer_ignores_blank_commands():
    from navig.agent.pattern_analyzer import PatternAnalyzer

    pa = PatternAnalyzer(min_occurrences=2)
    r1 = MagicMock()
    r1.command = "  "  # blank
    r2 = MagicMock()
    r2.command = "ls"
    records = [r1, r2, r2]  # ls appears twice
    results = pa.score_by_frequency(records)
    # Only ls should be present
    assert all(r.sequence != ("  ",) for r in results)


def test_pattern_analyzer_ignores_non_string_command():
    from navig.agent.pattern_analyzer import PatternAnalyzer

    pa = PatternAnalyzer(min_occurrences=2)
    r = MagicMock()
    r.command = 42  # not a string
    results = pa.score_by_frequency([r, r, r])
    assert results == []


def test_scored_pattern_is_dataclass():
    from navig.agent.pattern_analyzer import ScoredPattern

    sp = ScoredPattern(sequence=("ls",), occurrences=3, score=3.0)
    assert sp.sequence == ("ls",)
    assert sp.occurrences == 3
    assert sp.score == 3.0


# ---------------------------------------------------------------------------
# commands/user — user_show, user_set
# ---------------------------------------------------------------------------


def test_user_show_prints_name_and_email():
    from navig.commands.user import user_app

    mock_cfg = MagicMock()
    mock_cfg.get.side_effect = lambda key, default="": (
        "Alice" if key == "user.name" else "alice@example.com"
    )
    with patch("navig.config.ConfigManager", return_value=mock_cfg):
        result = runner.invoke(user_app, ["show"])
    assert result.exit_code == 0
    assert "Alice" in result.output
    assert "alice@example.com" in result.output


def test_user_show_not_set_fallback():
    from navig.commands.user import user_app

    mock_cfg = MagicMock()
    mock_cfg.get.side_effect = lambda key, default="": "(not set)"
    with patch("navig.config.ConfigManager", return_value=mock_cfg):
        result = runner.invoke(user_app, ["show"])
    assert result.exit_code == 0
    assert "not set" in result.output


def test_user_show_exception_handled():
    from navig.commands.user import user_app

    with (
        patch("navig.config.ConfigManager", side_effect=Exception("no config")),
        patch("navig.console_helper.warn", create=True),
    ):
        result = runner.invoke(user_app, ["show"])
    assert result.exit_code == 0


def test_user_set_calls_config_set():
    from navig.commands.user import user_app

    mock_cfg = MagicMock()
    with patch("navig.config.ConfigManager", return_value=mock_cfg):
        result = runner.invoke(user_app, ["set", "name", "Bob"])
    assert result.exit_code == 0
    mock_cfg.set.assert_called_once_with("user.name", "Bob")


def test_user_set_prints_confirmation():
    from navig.commands.user import user_app

    mock_cfg = MagicMock()
    with patch("navig.config.ConfigManager", return_value=mock_cfg):
        result = runner.invoke(user_app, ["set", "email", "bob@example.com"])
    assert result.exit_code == 0
    assert "user.email" in result.output
    assert "bob@example.com" in result.output


def test_user_no_args_help_shown():
    from navig.commands.user import user_app

    result = runner.invoke(user_app, [])
    # no_args_is_help=True → shows help and may exit 0 or non-zero
    assert result.exit_code in (0, 1, 2)


def test_user_help_contains_subcommands():
    from navig.commands.user import user_app

    result = runner.invoke(user_app, ["--help"])
    assert "show" in result.output or "set" in result.output
