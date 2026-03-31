from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from navig.cli import app

runner = CliRunner()


def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    from navig.config import reset_config_manager

    reset_config_manager()
    return tmp_path


def test_show_init_status_returns_expected_payload(tmp_path: Path, monkeypatch, capsys):
    home = _isolate_home(tmp_path, monkeypatch)
    navig_dir = home / ".navig"
    navig_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("NAVIG_LLM_PROVIDER", "openai")
    (navig_dir / ".telegram_configured").write_text("1", encoding="utf-8")
    (navig_dir / ".matrix_configured").write_text("1", encoding="utf-8")

    from navig.config import ConfigManager

    cm = ConfigManager(config_dir=navig_dir)
    cm.save_host_config(
        "localhost",
        {
            "name": "localhost",
            "host": "localhost",
            "user": "local",
            "is_local": True,
            "apps": {},
        },
    )

    from navig.commands.init import show_init_status

    payload = show_init_status()

    captured = capsys.readouterr()
    assert "NAVIG Init Status" in captured.out
    assert payload["provider"] == "openai"
    assert payload["hosts_count"] == 1
    assert payload["vault"] in {"initialized", "empty"}
    assert payload["integrations"]["telegram"] is True
    assert payload["integrations"]["matrix"] is True
    assert payload["integrations"]["email"] is False
    assert payload["python_version"]
    assert payload["navig_version"]


def test_init_status_flag_calls_show_init_status(monkeypatch):
    with patch("navig.commands.init.show_init_status", MagicMock()) as show_status, patch(
        "navig.onboarding.runner.run_engine_onboarding", MagicMock()
    ) as run_engine:
        result = runner.invoke(app, ["init", "--status"])

    assert result.exit_code == 0, result.output
    show_status.assert_called_once()
    run_engine.assert_not_called()


def test_init_already_configured_also_shows_init_status(monkeypatch):
    with patch("navig.commands.init.show_init_status", MagicMock()) as show_status, patch(
        "navig.commands.init._maybe_send_first_run_ping",
        MagicMock(),
        create=True,
    ), patch(
        "navig.onboarding.runner.run_engine_onboarding", MagicMock(return_value=None)
    ):
        result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert "already configured" in result.output.lower()
    show_status.assert_called_once()


def test_init_profile_quickstart_maps_to_operator_and_runs_chat_handoff(monkeypatch):
    with patch("navig.installer.run_install", MagicMock()) as run_install, patch(
        "navig.commands.init.run_chat_first_handoff", MagicMock()
    ) as run_handoff:
        result = runner.invoke(app, ["init", "--profile", "quickstart"])

    assert result.exit_code == 0, result.output
    run_install.assert_called_once_with(profile="operator", dry_run=False, quiet=False)
    run_handoff.assert_called_once_with(profile="quickstart", dry_run=False, quiet=False)
