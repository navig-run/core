"""`navig goal session …` — the session-goal sub-typer, and the non-breaking lock.

The standing-goal verbs (`add|list|show|run|pause|resume|remove`) own the `navig goal`
namespace; this file pins that they are still all there, that the new subcommands parse,
and that OS-down / disabled-loop failures are honest exits with actionable text — never
tracebacks. The RPC layer is monkeypatched at `navig.cloud.os_rpc.call` (the exact seam
`_os_call` imports), so no server is needed for the happy paths.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from navig.commands.goal import goal_app

runner = CliRunner()

STANDING_VERBS = ["add", "list", "show", "run", "pause", "resume", "remove"]
SESSION_VERBS = ["set", "show", "list", "pause", "resume", "clear", "complete"]


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Empty config dir + BOTH config singletons reset (the leak this file bit on).

    `navig.config.get` reads the `ConfigSingleton`, which keeps a frozen
    `_global_cache_path` across NAVIG_CONFIG_DIR changes; `reset_config_manager()`
    alone does not touch it, so one test's `goal.enabled: false` poisoned the next.
    """
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg_dir))
    reset_config_manager()
    monkeypatch.setattr(ConfigSingleton, "_instance", None, raising=False)
    yield cfg_dir
    reset_config_manager()
    ConfigSingleton._instance = None


@pytest.fixture
def os_calls(monkeypatch):
    """Record every RPC the CLI makes; default responder serves a two-session world."""
    from navig.cloud import os_rpc

    calls: list[tuple[str, tuple]] = []
    sessions: list[dict[str, Any]] = [
        {"id": "sess-alpha-001", "name": "Alpha", "goal": None},
        {"id": "sess-beta-002", "name": "Beta", "goal": {
            "id": "g-1", "objective": "keep the lights on", "phase": "active",
            "roundsStarted": 2, "maxRounds": 12, "revision": 3,
        }, "goalArmed": True},
    ]
    command_result: dict[str, Any] = {"goal": {
        "id": "g-new", "objective": "fresh", "phase": "active",
        "roundsStarted": 0, "maxRounds": 12, "revision": 1,
    }}

    def fake_call(channel: str, *args: Any, **kwargs: Any):
        calls.append((channel, args))
        if channel == "sessions:get":
            return sessions
        if channel == "sessions:command":
            return command_result
        raise AssertionError(f"unexpected channel {channel}")

    monkeypatch.setattr(os_rpc, "call", fake_call)
    return {"calls": calls, "sessions": sessions, "command_result": command_result}


def test_help_keeps_every_standing_verb():
    """CLI contract lock: adding the sub-typer must not drop a single standing verb."""
    result = runner.invoke(goal_app, ["--help"])
    assert result.exit_code == 0
    for verb in STANDING_VERBS:
        assert f" {verb}" in result.stdout, f"standing verb '{verb}' vanished from goal --help"
    assert "session" in result.stdout


def test_session_help_lists_all_six_verbs():
    result = runner.invoke(goal_app, ["session", "--help"])
    assert result.exit_code == 0
    for verb in SESSION_VERBS:
        assert verb in result.stdout, f"session verb '{verb}' missing from help"


def test_show_when_os_is_down_is_honest(monkeypatch):
    from navig.cloud import os_discovery

    monkeypatch.setattr(os_discovery, "read_descriptor", lambda path=None: None)
    result = runner.invoke(goal_app, ["session", "show", "whatever"])
    assert result.exit_code == 1
    assert "NAVIG OS is not running" in result.stdout


def test_set_refused_when_loop_disabled(isolated_config):
    # The exact string a `navig config set goal.enabled false` would leave behind.
    (isolated_config / "config.yaml").write_text(
        'goal:\n  enabled: "false"\n', encoding="utf-8"
    )
    result = runner.invoke(
        goal_app, ["session", "set", "sess-alpha-001", "do the thing"]
    )
    assert result.exit_code == 1
    assert "disabled" in result.stdout
    assert "goal.enabled true" in result.stdout


def test_set_sends_set_goal_command_with_default_budget(os_calls):
    result = runner.invoke(
        goal_app, ["session", "set", "sess-alpha-001", "Ship the release notes"]
    )
    assert result.exit_code == 0, result.stdout
    assert "Goal set" in result.stdout
    command_calls = [a for ch, a in os_calls["calls"] if ch == "sessions:command"]
    assert len(command_calls) == 1
    session_id, command = command_calls[0]
    assert session_id == "sess-alpha-001"
    assert command["type"] == "setGoal"
    assert command["objective"] == "Ship the release notes"
    assert command["maxRounds"] == 12  # goal.max_rounds default


def test_set_honors_max_rounds_flag(os_calls):
    result = runner.invoke(
        goal_app,
        ["session", "set", "sess-alpha-001", "obj", "--max-rounds", "3"],
    )
    assert result.exit_code == 0, result.stdout
    _, command = next(a for ch, a in os_calls["calls"] if ch == "sessions:command")
    assert command["maxRounds"] == 3


def test_show_json_shape(os_calls):
    result = runner.invoke(goal_app, ["session", "show", "sess-beta-002", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["sessionId"] == "sess-beta-002"
    assert payload["phase"] == "active"
    assert payload["roundsStarted"] == 2
    assert payload["maxRounds"] == 12
    assert payload["armed"] is True
    assert payload["objective"] == "keep the lights on"


def test_list_json_includes_only_sessions_with_goals(os_calls):
    result = runner.invoke(goal_app, ["session", "list", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert [row["sessionId"] for row in payload] == ["sess-beta-002"]
    assert payload[0]["phase"] == "active"


def test_resolve_by_exact_name(os_calls):
    result = runner.invoke(goal_app, ["session", "show", "Alpha", "--json"])
    # Alpha has no goal — resolution by name worked, the goal check fired.
    assert result.exit_code == 1
    assert "has no goal" in result.stdout


def test_resolve_ambiguous_prefix_exits_1(os_calls):
    os_calls["sessions"].append({"id": "sess-gamma-003", "name": "Gamma", "goal": None})
    result = runner.invoke(goal_app, ["session", "show", "sess-"])
    assert result.exit_code == 1
    assert "matches 3 sessions" in result.stdout


def test_resolve_no_match_exits_1(os_calls):
    result = runner.invoke(goal_app, ["session", "show", "nope"])
    assert result.exit_code == 1
    assert "no session matches" in result.stdout


def test_pause_and_clear_send_their_commands(os_calls):
    os_calls["command_result"] = {"goal": {
        "id": "g-1", "objective": "keep the lights on", "phase": "paused",
        "roundsStarted": 2, "maxRounds": 12, "revision": 4,
    }}
    result = runner.invoke(goal_app, ["session", "pause", "sess-beta-002"])
    assert result.exit_code == 0, result.stdout
    _, command = next(a for ch, a in os_calls["calls"] if ch == "sessions:command")
    assert command == {"type": "pauseGoal"}

    result = runner.invoke(goal_app, ["session", "clear", "sess-beta-002", "--yes"])
    assert result.exit_code == 0, result.stdout
    _, command = [a for ch, a in os_calls["calls"] if ch == "sessions:command"][-1]
    assert command == {"type": "clearGoal"}


def test_complete_requires_evidence_option(os_calls):
    result = runner.invoke(goal_app, ["session", "complete", "sess-beta-002"])
    assert result.exit_code != 0  # typer refuses: --evidence is required
    assert not any(
        ch == "sessions:command" for ch, _ in os_calls["calls"]
    ), "no command must reach the server without evidence"


def test_complete_sends_complete_goal_with_evidence(os_calls):
    # NOTE: mutate the fixture's command_result in place — the fake RPC returns
    # that object by reference; replacing the dict would leave the closure stale.
    os_calls["command_result"]["goal"] = {
        "id": "g-1", "objective": "keep the lights on", "phase": "complete",
        "roundsStarted": 2, "maxRounds": 12, "revision": 5,
        "completionNote": "closed by operator: suite green twice",
    }
    result = runner.invoke(goal_app, [
        "session", "complete", "sess-beta-002",
        "--evidence", "suite green twice; diff reviewed",
    ])
    assert result.exit_code == 0, result.stdout
    assert "Goal completed" in result.stdout
    _, command = next(a for ch, a in os_calls["calls"] if ch == "sessions:command")
    assert command == {
        "type": "completeGoal",
        "evidence": "suite green twice; diff reviewed",
    }


def test_complete_json_prints_the_goal(os_calls):
    os_calls["command_result"]["goal"] = {
        "id": "g-1", "objective": "lights", "phase": "complete",
        "roundsStarted": 1, "maxRounds": 12, "revision": 2,
        "completionNote": "closed by operator: verified on disk",
    }
    result = runner.invoke(goal_app, [
        "session", "complete", "sess-beta-002", "-e", "verified on disk", "--json",
    ])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["phase"] == "complete"
    assert "closed by operator" in payload["completionNote"]
