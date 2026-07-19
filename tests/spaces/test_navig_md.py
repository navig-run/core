"""NAVIG.md canonical context: scaffold, the migration matrix, config composition.

Run: cd core && python -m pytest tests/spaces/test_navig_md.py -q
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def cfg_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    return tmp_path


def test_fresh_scaffold_creates_navig_md_and_pointer(cfg_env):
    from navig.commands.space import _scaffold_space_skeleton

    proj = cfg_env / "fresh"
    proj.mkdir()
    _scaffold_space_skeleton(proj, "fresh", dry_run=False)

    navig_md = (proj / "NAVIG.md").read_text(encoding="utf-8")
    claude = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    assert "space: fresh" in navig_md
    assert "navig:agent-instructions:start" in navig_md
    assert "@NAVIG.md" in claude
    assert "navig:context:start" in claude


def test_migrate_legacy_claude_preserves_bytes(cfg_env):
    from navig.commands.space import _migrate_context

    proj = cfg_env / "legacy"
    (proj / ".navig").mkdir(parents=True)
    original = "# Hand written\n\nDo the thing.\n"
    (proj / "CLAUDE.md").write_text(original, encoding="utf-8")
    (proj / ".navig" / "ai_system_prompt.txt").write_text(
        "You are helpful.\n# ====== PROJECT VISION CONTEXT (auto-injected by NAVIG) ======\n"
        "Legacy is a migration demo.\n", encoding="utf-8")

    msgs = _migrate_context(proj, "legacy")
    new_claude = (proj / "CLAUDE.md").read_text(encoding="utf-8")

    assert (proj / "NAVIG.md").exists()
    assert new_claude.startswith(original.rstrip("\n"))  # bytes intact above
    assert "@NAVIG.md" in new_claude
    assert "Legacy is a migration demo." in (proj / "NAVIG.md").read_text(encoding="utf-8")
    assert any("NAVIG.md" in m for m in msgs)

    # idempotent
    assert _migrate_context(proj, "legacy") == []


def test_migrate_handrolled_pointer_untouched(cfg_env):
    from navig.commands.space import _migrate_context, _navig_md_template

    proj = cfg_env / "ref"
    proj.mkdir()
    (proj / "NAVIG.md").write_text(_navig_md_template("ref"), encoding="utf-8")
    before = "# hand\n\nSee NAVIG.md for the details.\n"
    (proj / "CLAUDE.md").write_text(before, encoding="utf-8")

    _migrate_context(proj, "ref")
    assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == before


def test_migrate_no_claude_creates_pointer(cfg_env):
    from navig.commands.space import _migrate_context

    proj = cfg_env / "nocl"
    proj.mkdir()
    _migrate_context(proj, "nocl")
    assert (proj / "NAVIG.md").exists()
    assert "@NAVIG.md" in (proj / "CLAUDE.md").read_text(encoding="utf-8")


def test_config_composes_navig_md(cfg_env, monkeypatch):
    from navig.commands.space import _navig_md_template

    proj = cfg_env / "comp"
    (proj / ".navig").mkdir(parents=True)
    (proj / "NAVIG.md").write_text(
        _navig_md_template("comp", vision_seed="Comp builds widgets."), encoding="utf-8")
    monkeypatch.chdir(proj)

    from navig.config import ConfigManager

    prompt = ConfigManager().get_ai_system_prompt()
    assert "## Project context (NAVIG.md)" in prompt
    assert "Comp builds widgets." in prompt
    assert "does not grant" in prompt  # safety framing present


def test_config_caps_project_context(cfg_env, monkeypatch):
    proj = cfg_env / "big"
    (proj / ".navig").mkdir(parents=True)
    (proj / "NAVIG.md").write_text("---\nspace: big\n---\n" + ("x" * 40000), encoding="utf-8")
    monkeypatch.chdir(proj)

    from navig.config import ConfigManager

    prompt = ConfigManager().get_ai_system_prompt()
    assert "truncated at 16" in prompt


def test_config_no_navig_md_is_legacy_verbatim(cfg_env, monkeypatch):
    proj = cfg_env / "plain"
    (proj / ".navig").mkdir(parents=True)
    monkeypatch.chdir(proj)

    from navig.config import ConfigManager

    prompt = ConfigManager().get_ai_system_prompt()
    assert "## Project context (NAVIG.md)" not in prompt
