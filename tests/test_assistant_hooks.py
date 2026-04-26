"""Hermetic unit tests for navig.assistant_hooks."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ctx(*, assistant_enabled: bool = True, yes: bool = False, verbose: bool = False, **kwargs) -> dict[str, Any]:
    return {"assistant_enabled": assistant_enabled, "yes": yes, "verbose": verbose, **kwargs}


# ---------------------------------------------------------------------------
# _resolve_assistant
# ---------------------------------------------------------------------------


class TestResolveAssistant:
    def test_calls_get_assistant_callable(self):
        from navig.assistant_hooks import _resolve_assistant

        sentinel = object()
        get_fn = MagicMock(return_value=sentinel)
        ctx = {"get_assistant": get_fn}
        result = _resolve_assistant(ctx)
        assert result is sentinel
        get_fn.assert_called_once_with(timeout=0.2)

    def test_falls_back_to_legacy_assistant_key(self):
        from navig.assistant_hooks import _resolve_assistant

        sentinel = object()
        ctx = {"assistant": sentinel}
        assert _resolve_assistant(ctx) is sentinel

    def test_returns_none_when_nothing_set(self):
        from navig.assistant_hooks import _resolve_assistant

        assert _resolve_assistant({}) is None

    def test_prefers_callable_over_legacy_key(self):
        from navig.assistant_hooks import _resolve_assistant

        callable_result = object()
        legacy_result = object()
        get_fn = MagicMock(return_value=callable_result)
        ctx = {"get_assistant": get_fn, "assistant": legacy_result}
        assert _resolve_assistant(ctx) is callable_result


# ---------------------------------------------------------------------------
# pre_execution_check
# ---------------------------------------------------------------------------


class TestPreExecutionCheck:
    def test_returns_true_when_assistant_disabled(self):
        from navig.assistant_hooks import pre_execution_check

        ctx = _ctx(assistant_enabled=False)
        assert pre_execution_check(ctx, "delete", {}) is True

    def test_returns_true_when_no_assistant_found(self):
        from navig.assistant_hooks import pre_execution_check

        ctx = _ctx()  # assistant_enabled=True but no actual assistant
        assert pre_execution_check(ctx, "restart", {}) is True

    def test_returns_true_when_yes_flag_set(self):
        from navig.assistant_hooks import pre_execution_check

        assistant = MagicMock()
        ctx = _ctx(yes=True, get_assistant=MagicMock(return_value=assistant))
        assert pre_execution_check(ctx, "delete", {}) is True
        # should not check warnings when --yes is set
        assistant.proactive_display.check_pre_execution_warnings.assert_not_called()

    def test_returns_true_when_assistant_check_raises(self):
        from navig.assistant_hooks import pre_execution_check

        assistant = MagicMock()
        assistant.proactive_display.check_pre_execution_warnings.side_effect = RuntimeError("boom")
        ctx = _ctx(get_assistant=MagicMock(return_value=assistant))
        assert pre_execution_check(ctx, "sql", {}) is True

    def test_returns_should_proceed_from_assistant(self):
        from navig.assistant_hooks import pre_execution_check

        assistant = MagicMock()
        assistant.proactive_display.check_pre_execution_warnings.return_value = (True, [])
        ctx = _ctx(get_assistant=MagicMock(return_value=assistant))
        assert pre_execution_check(ctx, "list", {}) is True

    def test_no_warnings_no_prompt_needed(self):
        from navig.assistant_hooks import pre_execution_check

        assistant = MagicMock()
        assistant.proactive_display.check_pre_execution_warnings.return_value = (True, [])
        ctx = _ctx(get_assistant=MagicMock(return_value=assistant))
        # with no warnings, should_proceed=True, no user input required
        result = pre_execution_check(ctx, "status", {})
        assert result is True


# ---------------------------------------------------------------------------
# post_execution_log
# ---------------------------------------------------------------------------


class TestPostExecutionLog:
    def test_returns_early_when_disabled(self):
        from navig.assistant_hooks import post_execution_log

        ctx = _ctx(assistant_enabled=False)
        # must not raise
        post_execution_log(ctx, "deploy", 0)

    def test_returns_early_when_no_assistant(self):
        from navig.assistant_hooks import post_execution_log

        ctx = _ctx()  # no assistant
        post_execution_log(ctx, "restart", 1, stderr="error text")  # must not raise

    def test_logs_command_when_success(self):
        from navig.assistant_hooks import post_execution_log

        assistant = MagicMock()
        assistant.should_auto_analyze.return_value = False
        ctx = _ctx(get_assistant=MagicMock(return_value=assistant))
        post_execution_log(ctx, "deploy", 0, stdout="ok", duration=1.5)
        assistant.auto_detection.log_command_execution.assert_called_once()

    def test_does_not_crash_when_log_raises(self):
        from navig.assistant_hooks import post_execution_log

        assistant = MagicMock()
        assistant.auto_detection.log_command_execution.side_effect = Exception("oops")
        ctx = _ctx(get_assistant=MagicMock(return_value=assistant))
        post_execution_log(ctx, "cmd", 0)  # must not raise


# ---------------------------------------------------------------------------
# analyze_and_suggest_solutions
# ---------------------------------------------------------------------------


class TestAnalyzeAndSuggestSolutions:
    def test_returns_early_when_disabled(self):
        from navig.assistant_hooks import analyze_and_suggest_solutions

        ctx = _ctx(assistant_enabled=False)
        analyze_and_suggest_solutions(ctx, "cmd", 1, "error")  # must not raise

    def test_returns_early_when_no_assistant(self):
        from navig.assistant_hooks import analyze_and_suggest_solutions

        ctx = _ctx()
        analyze_and_suggest_solutions(ctx, "cmd", 1, "error")  # must not raise

    def test_does_not_crash_when_analysis_raises(self):
        from navig.assistant_hooks import analyze_and_suggest_solutions

        assistant = MagicMock()
        assistant.error_resolution.analyze_error.side_effect = RuntimeError("fail")
        ctx = _ctx(verbose=True, get_assistant=MagicMock(return_value=assistant))
        analyze_and_suggest_solutions(ctx, "restart", 1, "some error")  # must not raise


# ---------------------------------------------------------------------------
# CommandTimer
# ---------------------------------------------------------------------------


class TestCommandTimer:
    def test_duration_measured(self):
        from navig.assistant_hooks import CommandTimer

        with CommandTimer() as t:
            pass  # near-instant

        assert t.duration >= 0.0

    def test_returns_self_from_enter(self):
        from navig.assistant_hooks import CommandTimer

        timer = CommandTimer()
        result = timer.__enter__()
        timer.__exit__(None, None, None)
        assert result is timer

    def test_exit_returns_false(self):
        from navig.assistant_hooks import CommandTimer

        timer = CommandTimer()
        timer.__enter__()
        assert timer.__exit__(None, None, None) is False
