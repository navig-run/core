"""Workspace formations + ``navig formation sync`` — the roster → Claude bridge.

Two contracts in one flow: a repo-tracked ``.navig/formations`` is discovered
(walked up from the invocation cwd — the long-promised project-local home), and
the active formation renders into ``.navig/agents/<id>.md`` in the exact
frontmatter shape scripts/sync-agents.py established, with a ``tools:`` line
ONLY when the agent's tools are all Claude-Code tool names (a navig ReAct tool
must never leak into Claude frontmatter). Idempotent; dry-run writes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.formation import formation_app
from navig.formations import loader as loader_mod

runner = CliRunner()

_TEAM = "team-sync-spec-x"


def _write_team(root: Path) -> Path:
    fdir = root / ".navig" / "formations" / _TEAM
    (fdir / "agents" / "reviewer").mkdir(parents=True)
    (fdir / "agents" / "worker").mkdir(parents=True)
    (fdir / "formation.json").write_text(
        json.dumps({
            "id": _TEAM,
            "name": "Team Sync",
            "version": "1.0.0",
            "description": "Roster under test",
            "agents": ["reviewer", "worker"],
            "default_agent": "reviewer",
        }),
        encoding="utf-8",
    )
    specs = {
        # All three names are inside the Claude Code tool set → tools line emitted.
        "reviewer": {"tools": ["Read", "Grep", "Bash"], "role": 'The reviewer — reads only, "signs"'},
        # No tools (and navig ReAct names would be rejected too) → line omitted.
        "worker": {"role": "Backend worker — writes inside its scope"},
    }
    for aid, extra in specs.items():
        d = fdir / "agents" / aid
        spec = {"id": aid, "name": aid.title(), **extra}
        (d / "agent.json").write_text(json.dumps(spec), encoding="utf-8")
        (d / "SOUL.md").write_text(f"# {aid}\nYou are the {aid} of the Grid.", encoding="utf-8")
        (d / "PERSONALITY.md").write_text("Calm, structural, brief.", encoding="utf-8")
        (d / "PLAYBOOK.md").write_text("Doors: your scope. Never: guess.", encoding="utf-8")
    return fdir


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    loader_mod.clear_formations_roots()
    # cwd ≠ invocation cwd: main.py chdirs a live CLI into the ACTIVE SPACE
    # before any command runs, so a read from Path.cwd() sees the space's own
    # .navig/profile.json (app_project here), while the operator — and
    # formation init — stand in the workspace (tmp_path). Sync must follow the
    # invocation, or a worktree's activation silently renders the space's roster
    # (the bug this split pins).
    space = tmp_path / "space-sim"
    (space / ".navig").mkdir(parents=True)
    (space / ".navig" / "profile.json").write_text(
        json.dumps({"version": 1, "profile": "app_project"}), encoding="utf-8"
    )
    monkeypatch.chdir(space)
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(tmp_path))
    yield
    loader_mod.clear_formations_roots()


def test_workspace_formations_are_discovered_by_walkup(tmp_path):
    fdir = _write_team(tmp_path)
    discovered = loader_mod.discover_formations()
    assert discovered[_TEAM] == fdir
    # Nearest workspace wins: discovery must not depend on the cwd's own name.
    nested = tmp_path / "sub" / "dir"
    nested.mkdir(parents=True)
    monkeypatch_nested = loader_mod.discover_formations()
    assert monkeypatch_nested[_TEAM] == fdir


def _activate(tmp_path: Path) -> None:
    (tmp_path / ".navig" / "profile.json").write_text(
        json.dumps({"version": 1, "profile": _TEAM}), encoding="utf-8"
    )


def test_sync_renders_claude_frontmatter_and_tools_subset(tmp_path):
    _write_team(tmp_path)
    _activate(tmp_path)

    res = runner.invoke(formation_app, ["sync", "--json"])
    assert res.exit_code == 0, res.output
    report = json.loads(res.output)
    assert report["formation"] == _TEAM
    assert len(report["written"]) == 2 and report["missing"] == []

    reviewer = (tmp_path / ".navig" / "agents" / "reviewer.md").read_text(encoding="utf-8")
    assert reviewer.startswith(
        '---\nname: reviewer\ndescription: "The reviewer — reads only, \\"signs\\""\n'
        "tools: Read, Grep, Bash\n---\n"
    )
    assert "GENERATED from formation " in reviewer
    assert "You are the reviewer of the Grid." in reviewer   # composed system_prompt

    worker = (tmp_path / ".navig" / "agents" / "worker.md").read_text(encoding="utf-8")
    assert worker.startswith('---\nname: worker\ndescription: "Backend worker — writes inside its scope"\n---\n')
    assert "tools:" not in worker.split("---", 2)[1]         # frontmatter block only


def test_sync_is_idempotent_and_dry_run_writes_nothing(tmp_path):
    _write_team(tmp_path)
    _activate(tmp_path)

    first = json.loads(runner.invoke(formation_app, ["sync", "--json"]).output)
    assert len(first["written"]) == 2
    target = tmp_path / ".navig" / "agents" / "worker.md"
    before = target.read_text(encoding="utf-8")

    second = json.loads(runner.invoke(formation_app, ["sync", "--json"]).output)
    assert second["written"] == [] and len(second["unchanged"]) == 2
    assert target.read_text(encoding="utf-8") == before      # byte-stable

    target.unlink()
    dry = json.loads(runner.invoke(formation_app, ["sync", "--dry-run", "--json"]).output)
    assert dry["dry_run"] is True and len(dry["written"]) == 1
    assert not target.exists()                               # wrote nothing


def test_sync_without_any_resolvable_formation_exits_nonzero(tmp_path):
    _write_team(tmp_path)          # discovered, but no .navig/profile.json
    # No profile falls back to the DEFAULT profile (documented loader behavior),
    # so to reach sync's own error path nothing may resolve at all.
    loader_mod.set_formations_roots([tmp_path / "empty-roots"])
    res = runner.invoke(formation_app, ["sync", "--json"])
    assert res.exit_code == 1


def test_a_traversal_agent_id_fails_formation_validation():
    """An agent id becomes `.navig/agents/<id>.md`; `../` must not validate — including
    without the optional jsonschema module, whose absence used to skip every pattern."""
    from navig.formations.schema import (
        FORMATION_SCHEMA,
        _validate_manually,
        validate_formation_data,
    )

    data = {
        "id": _TEAM, "name": "T", "version": "1.0.0", "description": "d",
        "agents": ["reviewer", "../../evil"], "default_agent": "reviewer",
    }
    _, errors = validate_formation_data(data)
    assert errors and any("../../evil" in e for e in errors), errors
    assert _validate_manually(data, FORMATION_SCHEMA), "the manual path must enforce it too"
    data["agents"] = ["reviewer", "worker_2", "front-end"]
    assert validate_formation_data(data)[1] == []


def test_sync_never_writes_outside_the_agents_dir(tmp_path, monkeypatch):
    """Defense in depth: a traversal id that reaches sync is reported, never written."""
    from types import SimpleNamespace

    import navig.formations.loader as loader

    agent = SimpleNamespace(role="evil", tools=[], system_prompt="pwned")
    formation = SimpleNamespace(
        id=_TEAM, agents=["../../evil"], loaded_agents={"../../evil": agent}
    )
    monkeypatch.setattr(loader, "get_active_formation", lambda **_k: formation)

    res = runner.invoke(formation_app, ["sync", "--json"])

    assert res.exit_code == 0, res.output
    report = json.loads(res.output)
    assert report["written"] == [] and report["missing"] == ["../../evil"]
    assert not (tmp_path / "evil.md").exists()
    assert not list(tmp_path.rglob("evil.md"))
