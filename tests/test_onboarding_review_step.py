"""
Tests for _step_review — onboarding summary and step-revisit flow.

Covers:
  - Phase-grouped summary output (completed / failed / skipped icons)
  - Confirm-yes → completed
  - Confirm-no + valid jump-to → skipped with jumpTo
  - Unknown step-ID validation loop
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.onboarding.steps import _step_review

# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_artifact(
    navig_dir: Path,
    steps: list[dict],
) -> None:
    """Write a minimal onboarding.json artifact."""
    (navig_dir / "onboarding.json").write_text(
        json.dumps({"steps": steps}),
        encoding="utf-8",
    )


def _artifact_steps(
    *entries: tuple[str, str],
) -> list[dict]:
    """Build step records from (id, status) pairs."""
    return [
        {
            "id": sid,
            "title": sid,
            "status": status,
            "completed_at": "2026-01-01T00:00:00",
            "duration_ms": 1,
            "output": {},
        }
        for sid, status in entries
    ]


# ── Tests ────────────────────────────────────────────────────────────────────


@patch("sys.stdin")
def test_confirm_yes_returns_completed(mock_stdin, tmp_path: Path) -> None:
    """When user confirms 'all good', result is completed."""
    _write_artifact(
        tmp_path,
        _artifact_steps(
            ("workspace-init", "completed"),
            ("ai-provider", "completed"),
        ),
    )
    step = _step_review(tmp_path)

    with (
        patch("navig.onboarding.steps._tty_check", return_value=None),
        patch("typer.confirm", return_value=True),
    ):
        result = step.run()

    assert result.status == "completed"
    assert result.output == {}


@patch("sys.stdin")
def test_confirm_no_with_valid_jump(mock_stdin, tmp_path: Path) -> None:
    """When user says 'no' and picks a valid step, result contains jumpTo."""
    _write_artifact(
        tmp_path,
        _artifact_steps(
            ("workspace-init", "completed"),
            ("ai-provider", "skipped"),
        ),
    )
    step = _step_review(tmp_path)

    with (
        patch("navig.onboarding.steps._tty_check", return_value=None),
        patch("typer.confirm", return_value=False),
        patch("typer.prompt", return_value="ai-provider"),
    ):
        result = step.run()

    assert result.status == "skipped"
    assert result.output.get("jumpTo") == "ai-provider"


@patch("sys.stdin")
def test_unknown_step_id_retries_then_accepts(mock_stdin, tmp_path: Path) -> None:
    """Unknown IDs are rejected; a subsequent valid ID is accepted."""
    _write_artifact(
        tmp_path,
        _artifact_steps(
            ("workspace-init", "completed"),
            ("ai-provider", "completed"),
        ),
    )
    step = _step_review(tmp_path)

    # First call returns unknown ID, second returns valid ID
    prompt_returns = iter(["bogus-step", "ai-provider"])

    with (
        patch("navig.onboarding.steps._tty_check", return_value=None),
        patch("typer.confirm", return_value=False),
        patch("typer.prompt", side_effect=lambda *a, **kw: next(prompt_returns)),
    ):
        result = step.run()

    assert result.status == "skipped"
    assert result.output.get("jumpTo") == "ai-provider"


@patch("sys.stdin")
def test_no_artifact_skips_summary(mock_stdin, tmp_path: Path) -> None:
    """When no onboarding.json exists, the step still works (no summary shown)."""
    step = _step_review(tmp_path)

    with (
        patch("navig.onboarding.steps._tty_check", return_value=None),
        patch("typer.confirm", return_value=True),
    ):
        result = step.run()

    assert result.status == "completed"


@patch("sys.stdin")
def test_phase_grouped_summary_shows_all_phases(
    mock_stdin, tmp_path: Path, capsys
) -> None:
    """Summary displays bootstrap, configuration, and integration phases."""
    _write_artifact(
        tmp_path,
        _artifact_steps(
            ("workspace-init", "completed"),
            ("config-file", "completed"),
            ("ai-provider", "completed"),
            ("telegram-bot", "skipped"),
        ),
    )
    titles = {
        "workspace-init": "Initialize workspace",
        "config-file": "Create config file",
        "ai-provider": "Choose AI provider",
        "telegram-bot": "Telegram bot setup",
    }
    step = _step_review(tmp_path, step_titles=titles)

    with (
        patch("navig.onboarding.steps._tty_check", return_value=None),
        patch("typer.confirm", return_value=True),
    ):
        result = step.run()

    assert result.status == "completed"
    captured = capsys.readouterr()
    assert "Bootstrap" in captured.out
    assert "Configuration" in captured.out
    assert "Integrations" in captured.out


def test_step_metadata() -> None:
    """Verify step ID, phase, and tier metadata."""
    step = _step_review(Path("/tmp/test"))
    assert step.id == "review"
    assert step.phase == "configuration"
    assert step.tier == "optional"
    assert step.on_failure == "skip"
