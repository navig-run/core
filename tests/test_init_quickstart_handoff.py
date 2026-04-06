from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from navig.cli import app
from navig.commands.init import (
    get_chat_onboarding_step_progress,
    mark_chat_onboarding_step_completed,
    run_chat_first_handoff,
)

runner = CliRunner()


def test_init_profile_quickstart_maps_to_operator_and_runs_chat_handoff() -> None:
    with patch("navig.installer.run_install", MagicMock()) as run_install, patch(
        "navig.commands.init.run_chat_first_handoff", MagicMock()
    ) as run_handoff:
        result = runner.invoke(app, ["init", "--profile", "quickstart"])

    assert result.exit_code == 0, result.output
    run_install.assert_called_once_with(profile="operator", dry_run=False, quiet=False)
    run_handoff.assert_called_once_with(profile="quickstart", dry_run=False, quiet=False)


def test_get_chat_onboarding_step_progress_defaults_to_pending(tmp_path) -> None:
    steps = get_chat_onboarding_step_progress(tmp_path / ".navig")
    assert [step["id"] for step in steps] == ["ai-provider", "first-host", "telegram-bot"]
    assert all(step["completed"] is False for step in steps)


def test_get_chat_onboarding_step_progress_reads_completed_steps(tmp_path) -> None:
    navig_dir = tmp_path / ".navig"
    artifact = navig_dir / "state" / "onboarding.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "steps": [
                    {"id": "ai-provider", "status": "completed"},
                    {"id": "first-host", "status": "failed"},
                    {"id": "telegram-bot", "status": "completed"},
                ]
            }
        ),
        encoding="utf-8",
    )

    steps = get_chat_onboarding_step_progress(navig_dir)
    by_id = {step["id"]: step for step in steps}
    assert by_id["ai-provider"]["completed"] is True
    assert by_id["first-host"]["completed"] is False
    assert by_id["telegram-bot"]["completed"] is True


def test_mark_chat_onboarding_step_completed_updates_artifact(tmp_path) -> None:
    navig_dir = tmp_path / ".navig"
    artifact = navig_dir / "state" / "onboarding.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "steps": [
                    {"id": "ai-provider", "status": "failed"},
                    {"id": "first-host", "status": "pending"},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert mark_chat_onboarding_step_completed("ai-provider", navig_dir) is True
    assert mark_chat_onboarding_step_completed("telegram-bot", navig_dir) is True

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    by_id = {step["id"]: step for step in payload["steps"]}
    assert by_id["ai-provider"]["status"] == "completed"
    assert by_id["telegram-bot"]["status"] == "completed"


def test_mark_chat_onboarding_step_completed_rejects_unknown_step(tmp_path) -> None:
    assert mark_chat_onboarding_step_completed("not-a-canonical-step", tmp_path / ".navig") is False


def test_run_chat_first_handoff_marks_telegram_step_on_auto_start_success(monkeypatch) -> None:
    marked: list[str] = []
    handoff_calls: list[dict] = []

    monkeypatch.setattr(
        "navig.messaging.secrets.resolve_telegram_bot_token",
        lambda _cfg=None: "123:token",
    )
    monkeypatch.setattr("navig.commands.init._auto_start_chat_runtime", lambda: True)
    monkeypatch.setattr(
        "navig.commands.init.mark_chat_onboarding_step_completed",
        lambda step_id, navig_dir=None: marked.append(step_id) or True,
    )
    monkeypatch.setattr(
        "navig.commands.init.write_chat_onboarding_handoff_state",
        lambda **kwargs: handoff_calls.append(kwargs),
    )

    run_chat_first_handoff(profile="quickstart", dry_run=False, quiet=True)

    assert marked == ["telegram-bot"]
    assert handoff_calls and handoff_calls[0]["auto_started"] is True
