from __future__ import annotations

from typer.testing import CliRunner

from navig.commands.agent import agent_app
from navig.spaces import normalize_space_name
import pytest

pytestmark = pytest.mark.integration

runner = CliRunner()


class _FakeStore:
    def __init__(self):
        self.state = None

    def get_ai_state(self, user_id):
        if self.state and self.state.get("user_id") == user_id:
            return self.state
        return None

    def set_ai_state(self, user_id, chat_id, mode, persona=None, context=None):
        self.state = {
            "user_id": user_id,
            "chat_id": chat_id,
            "mode": mode,
            "persona": persona,
            "context": context or {},
        }


def test_agent_continuation_continue_and_status(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: store)

    result = runner.invoke(
        agent_app,
        [
            "continuation",
            "continue",
            "--profile",
            "balanced",
            "--space",
            "finance",
        ],
    )
    assert result.exit_code == 0
    assert "Continuation enabled" in result.stdout
    assert "Policy: cooldown=10s, max_turns=3" in result.stdout
    assert "suppression(wait=30s, blocked=90s)" in result.stdout
    assert "decision=standard" in result.stdout

    status = runner.invoke(agent_app, ["continuation", "status"])
    assert status.exit_code == 0
    assert "profile=balanced" in status.stdout
    assert "Suppression windows: wait=30s, blocked=90s" in status.stdout
    assert "Decision sensitivity: standard" in status.stdout
    assert "Space focus: finance" in status.stdout


def test_agent_continuation_pause_and_skip(monkeypatch):
    store = _FakeStore()
    store.set_ai_state(
        user_id=0,
        chat_id=0,
        mode="active",
        persona="assistant",
        context={"continuation": {"enabled": True}},
    )
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: store)

    paused = runner.invoke(agent_app, ["continuation", "pause"])
    assert paused.exit_code == 0
    assert "paused" in paused.stdout.lower()

    skipped = runner.invoke(agent_app, ["continuation", "skip"])
    assert skipped.exit_code == 0
    assert "skipped" in skipped.stdout.lower()


def test_agent_continuation_start_alias(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr("navig.store.runtime.get_runtime_store", lambda: store)

    result = runner.invoke(
        agent_app,
        [
            "continuation",
            "start",
            "--profile",
            "aggressive",
            "--space",
            "ops",
        ],
    )
    assert result.exit_code == 0
    assert "Continuation enabled" in result.stdout
    assert "decision=eager" in result.stdout

    status = runner.invoke(agent_app, ["continuation", "status"])
    assert status.exit_code == 0
    assert "profile=aggressive" in status.stdout
    assert f"Space focus: {normalize_space_name('ops')}" in status.stdout
