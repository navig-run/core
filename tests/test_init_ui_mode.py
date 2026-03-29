from __future__ import annotations

from unittest.mock import MagicMock, patch

from navig.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def _patch_common_ping() -> patch:
    return patch(
        "navig.commands.init._maybe_send_first_run_ping",
        MagicMock(),
        create=True,
    )


def test_init_default_uses_engine_runner() -> None:
    with (
        _patch_common_ping(),
        patch("navig.onboarding.runner.run_engine_onboarding", MagicMock(return_value=object())) as run_engine,
        patch("navig.commands.onboard.run_onboard", MagicMock()) as run_onboard,
    ):
        result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    run_engine.assert_called_once()
    run_onboard.assert_not_called()


def test_init_tui_uses_tui_when_tty_available(monkeypatch) -> None:
    with (
        _patch_common_ping(),
        patch("navig.cli._init_tui_capable", return_value=True),
        patch("navig.commands.onboard.run_onboard", MagicMock()) as run_onboard,
        patch("navig.onboarding.runner.run_engine_onboarding", MagicMock()) as run_engine,
    ):
        result = runner.invoke(app, ["init", "--tui"])

    assert result.exit_code == 0, result.output
    run_onboard.assert_called_once_with(flow="auto")
    run_engine.assert_not_called()


def test_init_tui_falls_back_to_engine_when_not_tty(monkeypatch) -> None:
    with (
        _patch_common_ping(),
        patch("navig.cli._init_tui_capable", return_value=False),
        patch("navig.commands.onboard.run_onboard", MagicMock()) as run_onboard,
        patch("navig.onboarding.runner.run_engine_onboarding", MagicMock(return_value=object())) as run_engine,
    ):
        result = runner.invoke(app, ["init", "--tui"])

    assert result.exit_code == 0, result.output
    run_onboard.assert_not_called()
    run_engine.assert_called_once()


def test_init_env_tui_uses_tui(monkeypatch) -> None:
    monkeypatch.setenv("NAVIG_INIT_UI", "tui")

    with (
        _patch_common_ping(),
        patch("navig.cli._init_tui_capable", return_value=True),
        patch("navig.commands.onboard.run_onboard", MagicMock()) as run_onboard,
        patch("navig.onboarding.runner.run_engine_onboarding", MagicMock()) as run_engine,
    ):
        result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    run_onboard.assert_called_once_with(flow="auto")
    run_engine.assert_not_called()


def test_init_env_cli_forces_engine_even_with_tui_flag(monkeypatch) -> None:
    monkeypatch.setenv("NAVIG_INIT_UI", "cli")

    with (
        _patch_common_ping(),
        patch("navig.cli._init_tui_capable", return_value=True),
        patch("navig.commands.onboard.run_onboard", MagicMock()) as run_onboard,
        patch("navig.onboarding.runner.run_engine_onboarding", MagicMock(return_value=object())) as run_engine,
    ):
        result = runner.invoke(app, ["init", "--tui"])

    assert result.exit_code == 0, result.output
    run_onboard.assert_not_called()
    run_engine.assert_called_once()


def test_init_tui_provider_uses_manual_flow(monkeypatch) -> None:
    with (
        _patch_common_ping(),
        patch("navig.cli._init_tui_capable", return_value=True),
        patch("navig.commands.onboard.run_onboard", MagicMock()) as run_onboard,
        patch("navig.onboarding.runner.run_engine_onboarding", MagicMock()) as run_engine,
    ):
        result = runner.invoke(app, ["init", "--tui", "--provider"])

    assert result.exit_code == 0, result.output
    run_onboard.assert_called_once_with(flow="manual")
    run_engine.assert_not_called()
