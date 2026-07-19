"""Tests for navig.skills.autodetect — the deterministic project→skills engine
behind `navig skills auto`."""

from __future__ import annotations

import json
from pathlib import Path

from navig.skills.autodetect import (
    TECH_SKILLS,
    SkillPick,
    detect_stack,
    resolve_skills,
)


def _write(root: Path, rel: str, content: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_map_is_nonempty_and_well_formed():
    assert len(TECH_SKILLS) >= 20
    ids = [r.id for r in TECH_SKILLS]
    assert len(ids) == len(set(ids)), "tech ids must be unique"
    for r in TECH_SKILLS:
        assert r.skills, f"{r.id} has no skills"
        # every rule must have at least one detection signal
        assert r.packages or r.py_packages or r.config_files or r.file_extensions, r.id


def test_detects_js_packages(tmp_path: Path):
    _write(tmp_path, "package.json", json.dumps({"dependencies": {"next": "15", "react": "19"}}))
    ids = {r.id for r in detect_stack(tmp_path)}
    assert "nextjs" in ids
    assert "react" in ids


def test_detects_config_files(tmp_path: Path):
    _write(tmp_path, "Cargo.toml", "[package]\nname='x'")
    _write(tmp_path, "src-tauri/tauri.conf.json", "{}")
    ids = {r.id for r in detect_stack(tmp_path)}
    assert "rust" in ids
    assert "tauri" in ids


def test_detects_python_deps_word_boundary(tmp_path: Path):
    # fastapi present as a real dep; NOT matched inside a longer token.
    _write(tmp_path, "pyproject.toml", '[project]\ndependencies = ["fastapi>=0.1", "pydantic"]')
    ids = {r.id for r in detect_stack(tmp_path)}
    assert "fastapi" in ids
    assert "pydantic" in ids
    assert "python" in ids  # via pyproject.toml config file

    # a misleading token must NOT trigger fastapi
    other = tmp_path / "other"
    _write(other, "requirements.txt", "myfastapiwrapper==1.0\n")
    ids2 = {r.id for r in detect_stack(other)}
    assert "fastapi" not in ids2


def test_detects_file_extension(tmp_path: Path):
    _write(tmp_path, "deploy.sh", "#!/bin/bash\necho hi")
    ids = {r.id for r in detect_stack(tmp_path)}
    assert "bash" in ids


def test_resolve_skills_registry_and_community(tmp_path: Path):
    _write(tmp_path, "package.json", json.dumps({"dependencies": {"react": "19"}}))
    _write(tmp_path, "Dockerfile", "FROM alpine")
    picks = resolve_skills(detect_stack(tmp_path))
    specs = {p.spec for p in picks}
    # agent-skill resolves to the curated registry by NAME (owner/repo is attribution)
    assert "skill:midudev/autoskills/packages/autoskills/skills-registry/react-best-practices" in specs
    # community CLI skill resolves to the navig-run/community path
    assert "github:navig-run/community/cli-skills/docker/docker-ops" in specs
    assert all(isinstance(p, SkillPick) for p in picks)


def test_registry_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NAVIG_SKILLS_REGISTRY", "navig-run/community/skills-registry")
    _write(tmp_path, "package.json", json.dumps({"dependencies": {"react": "19"}}))
    specs = {p.spec for p in resolve_skills(detect_stack(tmp_path))}
    assert "skill:navig-run/community/skills-registry/react-best-practices" in specs


def test_walks_monorepo_workspaces(tmp_path: Path):
    # root manifest is empty of frameworks; the tech lives in a workspace package.
    _write(tmp_path, "pnpm-workspace.yaml", "packages:\n  - 'apps/*'\n  - '!apps/skip'\n")
    _write(tmp_path, "package.json", json.dumps({"name": "root"}))
    _write(tmp_path, "apps/web/package.json", json.dumps({"dependencies": {"next": "15"}}))
    _write(tmp_path, "apps/api/pyproject.toml", '[project]\ndependencies=["fastapi"]')
    _write(tmp_path, "apps/skip/package.json", json.dumps({"dependencies": {"vue": "3"}}))
    ids = {r.id for r in detect_stack(tmp_path)}
    assert "nextjs" in ids   # from apps/web
    assert "fastapi" in ids  # from apps/api
    # negations are ignored for detection → vue in the "skipped" pkg is still seen
    assert "vue" in ids


def test_resolve_dedupes(tmp_path: Path):
    # react + react-dom both present → react rule matches once, no dup skills
    _write(tmp_path, "package.json", json.dumps({"dependencies": {"react": "19", "react-dom": "19"}}))
    picks = resolve_skills(detect_stack(tmp_path))
    refs = [p.ref for p in picks]
    assert len(refs) == len(set(refs))


def test_empty_project_detects_nothing(tmp_path: Path):
    assert detect_stack(tmp_path) == []
    assert detect_stack(tmp_path / "does-not-exist") == []
