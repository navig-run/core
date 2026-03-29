from pathlib import Path

from navig.spaces.contracts import normalize_space_name
from navig.spaces.resolver import discover_space_paths, resolve_space


def test_normalize_space_name_aliases_and_default():
    assert normalize_space_name("health") == "health"
    assert normalize_space_name("health-space") == "health"
    assert normalize_space_name("devops") == "devops"
    assert normalize_space_name("ops") == "devops"
    assert normalize_space_name("sysops-space") == "sysops"
    assert normalize_space_name("") == "life"
    assert normalize_space_name("unknown-value") == "life"


def test_resolve_space_prefers_project_over_global(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    global_space = home / ".navig" / "spaces" / "health"
    global_space.mkdir(parents=True, exist_ok=True)

    repo = tmp_path / "repo"
    project_space = repo / ".navig" / "spaces" / "health"
    project_space.mkdir(parents=True, exist_ok=True)

    cfg = resolve_space("health", cwd=repo)
    assert cfg.scope == "project"
    assert cfg.path == project_space


def test_resolve_space_falls_back_to_global(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    global_space = home / ".navig" / "spaces" / "finance"
    global_space.mkdir(parents=True, exist_ok=True)

    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)

    cfg = resolve_space("finance", cwd=repo)
    assert cfg.scope == "global"
    assert cfg.path == global_space


def test_discover_space_paths_overrides_global_with_project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    (home / ".navig" / "spaces" / "health").mkdir(parents=True, exist_ok=True)
    (home / ".navig" / "spaces" / "career").mkdir(parents=True, exist_ok=True)

    repo = tmp_path / "repo"
    (repo / ".navig" / "spaces" / "health-space").mkdir(parents=True, exist_ok=True)

    discovered = discover_space_paths(cwd=repo)
    assert discovered["health"].scope == "project"
    assert discovered["career"].scope == "global"
