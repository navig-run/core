"""CLI smoke — `navig coordination` runs the same Core operations the deck API and MCP use."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from navig.commands.coordination import coordination_app
from navig.store.coordination import CoordinationStore


@pytest.fixture
def store(tmp_path):  # noqa: ANN001
    return CoordinationStore(tmp_path / "coordination.db")


def _last_json(output: str) -> dict:
    """The final line the CLI emits for `--json` runs."""
    lines = [ln for ln in output.splitlines() if ln.strip()]
    return json.loads(lines[-1])


def test_send_then_inbox_round_trip(monkeypatch, store) -> None:
    monkeypatch.setattr("navig.coordination.get_coordination_store", lambda: store)
    runner = CliRunner()

    sent = runner.invoke(
        coordination_app,
        ["send", "session:1", "--content", '{"note": "hi"}', "--sender", "operator", "--json"],
    )
    assert sent.exit_code == 0, sent.output
    message = _last_json(sent.output)
    assert message["recipient"] == "session:1"
    assert message["delivery_state"] == "persisted"

    inbox = runner.invoke(coordination_app, ["inbox", "session:1", "--json"])
    assert inbox.exit_code == 0, inbox.output
    data = _last_json(inbox.output)
    assert data["count"] == 1
    assert data["messages"][0]["content"] == {"note": "hi"}


def test_transition_rejects_an_unknown_verb(monkeypatch, store) -> None:
    monkeypatch.setattr("navig.coordination.get_coordination_store", lambda: store)
    runner = CliRunner()
    result = runner.invoke(coordination_app, ["transition", "m1", "explode"])
    assert result.exit_code == 1
    assert "unknown transition" in result.output


def test_send_rejects_invalid_json_content(monkeypatch, store) -> None:
    monkeypatch.setattr("navig.coordination.get_coordination_store", lambda: store)
    runner = CliRunner()
    result = runner.invoke(coordination_app, ["send", "session:1", "--content", "not-json"])
    assert result.exit_code == 1
    assert "not valid JSON" in result.output
