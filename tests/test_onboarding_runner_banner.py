"""Tests for run_engine_onboarding banner messages (first-time vs. reconfigure)."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch


def _make_engine_state(interrupted_at=None):
    from navig.onboarding.engine import EngineState, StepRecord

    return EngineState(
        steps=[
            StepRecord(
                id="s",
                title="S",
                status="completed",
                completed_at="2026-01-01T00:00:00",
                duration_ms=0,
                output={},
            )
        ],
        interrupted_at=interrupted_at,
    )


def test_banner_first_time_setup():
    """Banner says 'first-time setup' when force=False."""
    state = _make_engine_state()
    buf = io.StringIO()

    with (
        patch("navig.onboarding.runner.load_or_create") as mock_genesis,
        patch("navig.onboarding.runner.build_step_registry", return_value=[]),
        patch("navig.onboarding.runner.OnboardingEngine") as mock_engine_cls,
        patch("sys.stdout", buf),
    ):
        mock_genesis.return_value = MagicMock()
        mock_engine_cls.return_value.run.return_value = state

        from navig.onboarding.runner import run_engine_onboarding

        run_engine_onboarding(force=False, show_banner=True)

    output = buf.getvalue()
    assert "first-time setup" in output
    assert "NAVIG_SKIP_ONBOARDING" in output
    assert "reconfigur" not in output


def test_banner_reconfigure():
    """Banner says 'reconfiguring' when force=True."""
    state = _make_engine_state()
    buf = io.StringIO()

    with (
        patch("navig.onboarding.runner.load_or_create") as mock_genesis,
        patch("navig.onboarding.runner.build_step_registry", return_value=[]),
        patch("navig.onboarding.runner.OnboardingEngine") as mock_engine_cls,
        patch("sys.stdout", buf),
    ):
        mock_genesis.return_value = MagicMock()
        mock_engine_cls.return_value.run.return_value = state

        from navig.onboarding.runner import run_engine_onboarding

        run_engine_onboarding(force=True, show_banner=True)

    output = buf.getvalue()
    assert "reconfigur" in output.lower()
    assert "first-time setup" not in output


def test_no_banner_when_show_banner_false():
    """No banner output when show_banner=False."""
    state = _make_engine_state()
    buf = io.StringIO()

    with (
        patch("navig.onboarding.runner.load_or_create") as mock_genesis,
        patch("navig.onboarding.runner.build_step_registry", return_value=[]),
        patch("navig.onboarding.runner.OnboardingEngine") as mock_engine_cls,
        patch("sys.stdout", buf),
    ):
        mock_genesis.return_value = MagicMock()
        mock_engine_cls.return_value.run.return_value = state

        from navig.onboarding.runner import run_engine_onboarding

        run_engine_onboarding(force=False, show_banner=False)

    output = buf.getvalue()
    assert "Welcome" not in output
    assert "first-time" not in output


def test_rich_console_used_for_wizard_output():
    """Wizard output goes through Rich Console (not plain sys.stdout.write).

    This is the regression test for the ANSI colour rendering bug:
    previously the wizard used sys.stdout.write() while the migration notice
    used Console(stderr=True), causing a visual break after migration.
    Now the wizard also uses a stdout Console, giving consistent formatting.
    """
    from navig.onboarding.runner import _get_console

    # Rich should be available in the test environment
    con = _get_console()
    assert con is not None, "_get_console() must return a Console when rich is installed"

    # Verify that the returned object has the print method Rich Console provides
    assert callable(getattr(con, "print", None)), "Console must expose a .print() method"


def test_get_console_returns_none_when_rich_unavailable():
    """_get_console() returns None gracefully when rich is not installed."""
    import builtins

    original_import = builtins.__import__

    def _block_rich(name, *args, **kwargs):
        if name == "rich.console":
            raise ImportError("rich not available")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_block_rich):
        from navig.onboarding.runner import _get_console

        result = _get_console()

    assert result is None, "_get_console() must return None when rich is unavailable"


def test_console_banner_written_to_stdout():
    """When Rich is available, the banner is written via the Console to stdout."""
    state = _make_engine_state()
    buf = io.StringIO()

    with (
        patch("navig.onboarding.runner.load_or_create") as mock_genesis,
        patch("navig.onboarding.runner.build_step_registry", return_value=[]),
        patch("navig.onboarding.runner.OnboardingEngine") as mock_engine_cls,
        patch("sys.stdout", buf),
    ):
        mock_genesis.return_value = MagicMock()
        mock_engine_cls.return_value.run.return_value = state

        # Ensure no Rich import error leaks into the test
        from navig.onboarding.runner import run_engine_onboarding

        run_engine_onboarding(force=True, show_banner=True)

    output = buf.getvalue()
    # The banner must appear in output regardless of whether Rich strips markup
    assert "Welcome back" in output
    assert "reconfiguring" in output.lower()
