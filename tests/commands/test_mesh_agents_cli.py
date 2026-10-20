"""``navig mesh agents`` — the session/owner registry (T-036 / NAV-016).

Pins: one entry per session (a second claim updates, never duplicates), unknown
roles and undetectable sessions are refused with nonzero exits, release is kept
as history, and staleness is an advisory read — nothing is ever reaped.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from navig import agent_registry
from navig.commands.mesh import mesh_app

runner = CliRunner()

_SESS = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _SESS)


def test_claim_upserts_one_entry_per_session():
    first = runner.invoke(
        mesh_app,
        ["agents", "claim", "--role", "backend-worker", "--worktree", ".dev/worktrees/x", "--json"],
    )
    assert first.exit_code == 0, first.output
    row = json.loads(first.output)
    assert row["session_id"] == _SESS and row["role"] == "backend-worker"

    second = runner.invoke(
        mesh_app,
        ["agents", "claim", "--role", "backend-worker", "--card", "deadbeef0001", "--json"],
    )
    assert second.exit_code == 0
    assert json.loads(second.output)["card"] == "deadbeef0001"

    listed = json.loads(runner.invoke(mesh_app, ["agents", "list", "--json"]).output)
    assert listed["count"] == 1                     # upsert, not append
    entry = listed["agents"][0]
    assert entry["card"] == "deadbeef0001"
    assert entry["worktree"] == ".dev/worktrees/x"  # kept from the first claim
    assert entry["status"] == "active"
    assert entry["stale"] is False


def test_unknown_role_refused():
    res = runner.invoke(mesh_app, ["agents", "claim", "--role", "wizard"])
    assert res.exit_code == 1
    assert "unknown role" in res.output


def test_claim_without_a_detectable_session_refused(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr(agent_registry, "current_session_id", lambda: "")
    res = runner.invoke(mesh_app, ["agents", "claim", "--role", "orchestrator"])
    assert res.exit_code == 1
    assert "session id" in res.output


def test_release_keeps_history_and_filters_to_active():
    assert runner.invoke(mesh_app, ["agents", "claim", "--role", "reviewer"]).exit_code == 0

    rel = runner.invoke(mesh_app, ["agents", "release", "--json"])
    assert rel.exit_code == 0
    assert json.loads(rel.output)["status"] == "released"

    active = json.loads(runner.invoke(mesh_app, ["agents", "list", "--active", "--json"]).output)
    assert active["count"] == 0
    all_rows = json.loads(runner.invoke(mesh_app, ["agents", "list", "--json"]).output)
    assert all_rows["count"] == 1
    assert all_rows["agents"][0]["status"] == "released"


def test_release_without_an_entry_is_an_error():
    res = runner.invoke(mesh_app, ["agents", "release"])
    assert res.exit_code == 1
    assert "No registry entry" in res.output


def test_old_entries_read_as_stale_advisory():
    assert runner.invoke(mesh_app, ["agents", "claim", "--role", "orchestrator"]).exit_code == 0
    data = agent_registry.load()
    data["agents"][0]["last_seen"] = "2020-01-01T00:00:00+00:00"
    agent_registry._save(data)  # noqa: SLF001 - pinning the on-disk contract

    listed = json.loads(runner.invoke(mesh_app, ["agents", "list", "--json"]).output)
    assert listed["agents"][0]["stale"] is True
    assert listed["agents"][0]["age_seconds"] > 0
    # Advisory only: a stale claim is still listed and still status=active.
    assert listed["agents"][0]["status"] == "active"
