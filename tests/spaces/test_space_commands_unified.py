from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.space import space_app

pytestmark = pytest.mark.integration

runner = CliRunner()


class _FakeConfigManager:
    def __init__(self, base: Path, global_config: dict | None = None):
        self.global_config_dir = str(base)
        self.global_config = global_config or {}


def test_space_create_switch_current_list_and_delete(tmp_path, monkeypatch):
    global_cfg = tmp_path / "global"
    fake_cm = _FakeConfigManager(global_cfg)
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cm)

    created = runner.invoke(space_app, ["create", "work"])
    assert created.exit_code == 0
    assert (global_cfg / "spaces" / "work").is_dir()

    switched = runner.invoke(space_app, ["use", "work"])
    assert switched.exit_code == 0
    assert "Active space: work" in switched.stdout

    current = runner.invoke(space_app, ["current"])
    assert current.exit_code == 0
    assert "Active space: work" in current.stdout

    listed = runner.invoke(space_app, ["list"])
    assert listed.exit_code == 0
    assert "work" in listed.stdout

    deleted = runner.invoke(space_app, ["delete", "work", "--yes"])
    assert deleted.exit_code == 0
    assert not (global_cfg / "spaces" / "work").exists()

    current_after = runner.invoke(space_app, ["current"])
    assert current_after.exit_code == 0
    assert (
        "default" in current_after.stdout
    )  # displayed as "My Space (default)" or "Active space: default"


def test_space_create_invalid_slug_shows_builtin_hint(tmp_path, monkeypatch):
    global_cfg = tmp_path / "global"
    fake_cm = _FakeConfigManager(global_cfg)
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cm)

    result = runner.invoke(space_app, ["create", "Bad_Name"])
    assert result.exit_code != 0
    combined = (
        (result.stdout or "")
        + (getattr(result, "stderr", "") or "")
        + (getattr(result, "output", "") or "")
    )
    assert "Invalid space name" in combined
    assert "Common spaces" in combined


def test_space_current_prefers_env_var(tmp_path, monkeypatch):
    global_cfg = tmp_path / "global"
    fake_cm = _FakeConfigManager(global_cfg, {"space": {"active": "work"}})
    monkeypatch.setattr("navig.commands.space.get_config_manager", lambda: fake_cm)
    monkeypatch.setenv("NAVIG_SPACE", "focus")

    result = runner.invoke(space_app, ["current"])
    assert result.exit_code == 0
    assert "Active space: focus" in result.stdout


def test_space_init_scaffolds_full_structure(tmp_path):
    dest = tmp_path / "demo"
    result = runner.invoke(space_app, ["init", "demo", "--path", str(dest)])
    assert result.exit_code == 0
    # canonical state dirs + plans + hygiene zones
    assert (dest / ".navig" / "plans" / "CURRENT_PHASE.md").is_file()
    assert (dest / ".navig" / "inbox").is_dir()
    assert (dest / ".dev").is_dir() and (dest / ".local").is_dir() and (dest / "docs").is_dir()
    assert (dest / ".navig" / "space.config.json").is_file()
    # root links resolve into .navig (junction on Windows, symlink on POSIX)
    assert (dest / "plans" / "VISION.md").exists()
    assert (dest / "inbox" / "docs").exists()
    # .local is gitignored
    assert ".local/" in (dest / ".gitignore").read_text()


def test_space_init_is_purely_additive(tmp_path):
    dest = tmp_path / "proj"
    # a user file that must survive untouched
    (dest / ".navig" / "plans").mkdir(parents=True)
    sentinel = dest / ".navig" / "plans" / "CURRENT_PHASE.md"
    sentinel.write_text("USER CONTENT — KEEP")
    # a FILE sitting where a folder belongs must not be clobbered
    (dest / ".dev").write_text("i am a file")

    result = runner.invoke(space_app, ["init", "proj", "--path", str(dest)])
    assert result.exit_code == 0
    assert sentinel.read_text() == "USER CONTENT — KEEP"   # never overwritten
    assert (dest / ".dev").is_file()                        # conflict, not clobbered
    assert (dest / ".dev").read_text() == "i am a file"
    assert "conflict" in result.output.lower()

    # idempotent: a second pass creates nothing new
    from navig.commands.space import _scaffold_space_skeleton

    summary = _scaffold_space_skeleton(dest, "proj")
    assert summary["created"] == []


def test_space_init_dry_run_writes_nothing(tmp_path):
    dest = tmp_path / "ghost"
    result = runner.invoke(space_app, ["init", "ghost", "--path", str(dest), "--dry-run"])
    assert result.exit_code == 0
    assert "Would create" in result.output
    assert not dest.exists()  # nothing written
