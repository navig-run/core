from __future__ import annotations

from pathlib import Path

from navig import workspace as workspace_module
from navig import workspace_ownership as own
from navig.commands import onboard
from navig.workspace import WorkspaceManager


def test_detect_project_workspace_duplicates(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_ws = project_root / ".navig" / "workspace"
    user_ws = tmp_path / "user" / ".navig" / "workspace"
    project_ws.mkdir(parents=True)
    user_ws.mkdir(parents=True)

    (project_ws / "SOUL.md").write_text("project-soul", encoding="utf-8")
    (user_ws / "SOUL.md").write_text("user-soul", encoding="utf-8")
    (project_ws / "HEARTBEAT.md").write_text("same", encoding="utf-8")
    (user_ws / "HEARTBEAT.md").write_text("same", encoding="utf-8")
    (project_ws / "PERSONA.md").write_text("legacy-only", encoding="utf-8")

    duplicates = own.detect_project_workspace_duplicates(
        project_root=project_root,
        user_workspace=user_ws,
    )
    statuses = {d.file_name: d.status for d in duplicates}

    assert statuses["SOUL.md"] == "duplicate_conflict"
    assert statuses["HEARTBEAT.md"] == "duplicate_identical"
    assert statuses["PERSONA.md"] == "project_only_legacy"


def test_resolve_personal_workspace_path_uses_user_workspace_for_all_noncanonical_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    user_ws = tmp_path / "home" / ".navig" / "workspace"
    project_root = tmp_path / "project"
    project_ws = project_root / ".navig" / "workspace"
    custom_ws = tmp_path / "custom" / "workspace"

    monkeypatch.setattr(own, "USER_WORKSPACE_DIR", user_ws)

    canonical, legacy = own.resolve_personal_workspace_path(
        project_ws, project_root=project_root
    )
    assert canonical == user_ws
    assert legacy == project_ws

    canonical, legacy = own.resolve_personal_workspace_path(
        custom_ws, project_root=project_root
    )
    assert canonical == user_ws
    assert legacy == custom_ws


def test_workspace_manager_prefers_user_workspace_and_uses_legacy_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    user_ws = tmp_path / "home" / ".navig" / "workspace"
    project_root = tmp_path / "project"
    project_ws = project_root / ".navig" / "workspace"
    user_ws.mkdir(parents=True)
    project_ws.mkdir(parents=True)

    # Redirect module defaults to temp locations.
    monkeypatch.setattr(workspace_module, "DEFAULT_NAVIG_DIR", user_ws.parent)
    monkeypatch.setattr(workspace_module, "DEFAULT_WORKSPACE_DIR", user_ws)
    monkeypatch.setattr(
        workspace_module, "DEFAULT_CONFIG_FILE", user_ws.parent / "navig.json"
    )
    monkeypatch.setattr(own, "USER_WORKSPACE_DIR", user_ws)

    (user_ws / "USER.md").write_text("user copy", encoding="utf-8")
    (project_ws / "USER.md").write_text("project copy", encoding="utf-8")

    wm = WorkspaceManager(
        workspace_path=project_ws, config_path=tmp_path / "missing.json"
    )
    assert wm.workspace_path == user_ws
    assert wm.legacy_workspace_path == project_ws
    assert wm.get_file_content("USER.md") == "user copy"

    (user_ws / "USER.md").unlink()
    assert wm.get_file_content("USER.md") == "project copy"


def test_create_workspace_templates_writes_personal_files_to_user_workspace_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    user_ws = tmp_path / "home" / ".navig" / "workspace"
    project_ws = tmp_path / "project" / ".navig" / "workspace"
    project_ws.mkdir(parents=True)

    monkeypatch.setattr(onboard, "USER_WORKSPACE_DIR", user_ws)
    monkeypatch.setattr(
        onboard, "detect_project_workspace_duplicates", lambda project_root=None: []
    )

    onboard.create_workspace_templates(project_ws, console=None)

    assert (user_ws / "SOUL.md").exists()
    assert (user_ws / "HEARTBEAT.md").exists()
    assert not (project_ws / "SOUL.md").exists()
    assert not (project_ws / "HEARTBEAT.md").exists()
