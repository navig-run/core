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
