from pathlib import Path

import pytest

from navig.migrations.workspace_to_spaces import (
    ensure_no_stale_spaces_registration,
    migrate_workspace_to_spaces,
)


def test_migrate_workspace_to_spaces_moves_payload_and_updates_config(tmp_path):
    navig_root = tmp_path / ".navig"
    old_workspace = navig_root / "workspace"
    old_workspace.mkdir(parents=True, exist_ok=True)
    (old_workspace / "remediation_actions.json").write_text("{}", encoding="utf-8")
    (old_workspace / "config-backup").mkdir(parents=True, exist_ok=True)

    config_file = navig_root / "config.yaml"
    config_file.write_text("spaces:\n  active: studio\n", encoding="utf-8")

    messages: list[str] = []
    migrate_workspace_to_spaces(navig_root, notify=messages.append)

    target = navig_root / "spaces" / "default" / "legacy-workspace"
    assert (target / "remediation_actions.json").exists()
    assert (target / "config-backup").is_dir()
    assert not old_workspace.exists()

    cache = navig_root / "cache" / "active_space.txt"
    assert cache.read_text(encoding="utf-8") == "studio"

    cfg = config_file.read_text(encoding="utf-8")
    assert "active_space: studio" in cfg
    assert "space:" in cfg
    assert "active: studio" in cfg
    assert "spaces:\n  active" not in cfg

    sentinel = navig_root / ".workspace_to_spaces.migrated"
    assert sentinel.exists()
    assert messages


def test_migration_is_idempotent_with_sentinel(tmp_path):
    navig_root = tmp_path / ".navig"
    navig_root.mkdir(parents=True, exist_ok=True)
    (navig_root / ".workspace_to_spaces.migrated").write_text("done", encoding="utf-8")

    migrate_workspace_to_spaces(navig_root)
    assert (navig_root / ".workspace_to_spaces.migrated").exists()


def test_migration_normalizes_agent_workspace_even_with_sentinel(tmp_path):
    navig_root = tmp_path / ".navig"
    navig_root.mkdir(parents=True, exist_ok=True)
    (navig_root / ".workspace_to_spaces.migrated").write_text("done", encoding="utf-8")

    old_workspace = navig_root / "workspace"
    config_file = navig_root / "config.yaml"
    config_file.write_text(
        "active_space: help\nagents:\n  defaults:\n    workspace: "
        + str(old_workspace).replace("\\", "\\\\")
        + "\n",
        encoding="utf-8",
    )

    migrate_workspace_to_spaces(navig_root)

    cfg = config_file.read_text(encoding="utf-8")
    assert "workspace:" in cfg
    assert str(navig_root / "spaces" / "help") in cfg


def test_ensure_no_stale_spaces_registration_raises(monkeypatch):
    import navig.cli as cli

    stale_map = dict(cli._EXTERNAL_CMD_MAP)
    stale_map["spaces"] = ("navig.commands.spaces", "spaces_context_app")
    monkeypatch.setattr(cli, "_EXTERNAL_CMD_MAP", stale_map)

    with pytest.raises(RuntimeError):
        ensure_no_stale_spaces_registration()
