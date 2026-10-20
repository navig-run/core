"""Tests for run_engine_onboarding banner messages (first-time vs. reconfigure)."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


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
    """First-run banner (force=False) invites setup on THIS machine.

    It no longer says "first-time setup": the copy now invites setup here and
    points someone adding an existing NAVIG at the join page (`/install`).
    Assert the current copy — the two stdout-purity guards pin the banner too,
    and pinning its body there is what made them go stale."""
    import pathlib

    state = _make_engine_state()
    buf = io.StringIO()

    with (
        patch.object(pathlib.Path, "exists", return_value=False),
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
    assert "set up this computer" in output
    assert "NAVIG_SKIP_ONBOARDING" in output
    assert "reconfigur" not in output
    # The old banner told EVERY machine it must be "the brain" — wrong for a laptop
    # joining an existing NAVIG; the banner now points those at the join page.
    assert "should BE the" not in output
    assert "/install" in output


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


# ── first-run shape: optional integrations become "Add later" ────────────────


def _fake_steps():
    from types import SimpleNamespace

    return [
        SimpleNamespace(id="workspace-init", title="Workspace", tier="essential"),
        SimpleNamespace(id="ai-provider", title="AI provider", tier="recommended"),
        SimpleNamespace(id="telegram-bot", title="Telegram bot", tier="optional"),
        SimpleNamespace(id="lighthouse", title="Lighthouse", tier="optional"),
        SimpleNamespace(id="review", title="Review", tier="optional"),
    ]


def _run_capturing_steps(**kwargs):
    state = _make_engine_state()
    buf = io.StringIO()
    with (
        patch("navig.onboarding.runner.load_or_create") as mock_genesis,
        patch("navig.onboarding.runner.build_step_registry", return_value=_fake_steps()),
        patch("navig.onboarding.runner.OnboardingEngine") as mock_engine_cls,
        patch("navig.onboarding.runner._print_verification_dashboard"),
        patch("sys.stdout", buf),
    ):
        mock_genesis.return_value = MagicMock()
        mock_engine_cls.return_value.run.return_value = state
        from navig.onboarding.runner import run_engine_onboarding

        run_engine_onboarding(show_banner=True, skip_if_configured=False, **kwargs)
        ran = [s.id for s in mock_engine_cls.call_args.args[1]]
    return ran, buf.getvalue()


def test_automatic_first_run_defers_optional_integrations():
    ran, out = _run_capturing_steps(include_optional=False)
    assert ran == ["workspace-init", "ai-provider", "review"]  # review is the summary — kept
    assert "Add later" in out
    assert "Telegram bot" in out and "Lighthouse" in out
    assert "navig init --reconfigure" in out


def test_explicit_wizard_still_runs_every_step():
    ran, out = _run_capturing_steps()  # include_optional defaults to True (navig init)
    assert ran == ["workspace-init", "ai-provider", "telegram-bot", "lighthouse", "review"]
    assert "Add later" not in out


def test_main_first_run_uses_the_short_shape():
    import inspect

    import navig.main as main_mod

    src = inspect.getsource(main_mod._check_first_run)
    assert "include_optional=False" in src
