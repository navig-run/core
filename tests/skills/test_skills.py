"""Tests for skills command helpers."""

from pathlib import Path

import pytest

from navig.commands.skills import list_skills_cmd, tree_skills_cmd

pytestmark = pytest.mark.integration


def _write_skill(path: Path, name: str, description: str) -> None:
    content = """---
name: {name}
description: {description}
---

# {title}
""".format(name=name, description=description, title=name.replace("-", " ").title())
    path.write_text(content, encoding="utf-8")


def test_list_skills_collects_metadata(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skill_a = skills_dir / "server-management" / "disk-space" / "SKILL.md"
    skill_b = skills_dir / "meta" / "create-skill" / "SKILL.md"

    skill_a.parent.mkdir(parents=True)
    skill_b.parent.mkdir(parents=True)

    _write_skill(skill_a, "disk-space", "Check disk usage")
    _write_skill(skill_b, "create-skill", "Guide for creating skills")

    skills = list_skills_cmd({"skills_dir": str(skills_dir), "plain": True})

    names = {skill.name for skill in skills}
    categories = {skill.category for skill in skills}

    assert names == {"disk-space", "create-skill"}
    assert categories == {"server-management", "meta"}


def test_tree_skills_groups_by_category(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skill_a = skills_dir / "server-management" / "disk-space" / "SKILL.md"
    skill_b = skills_dir / "server-management" / "system-status" / "SKILL.md"
    skill_c = skills_dir / "meta" / "create-skill" / "SKILL.md"

    skill_a.parent.mkdir(parents=True)
    skill_b.parent.mkdir(parents=True)
    skill_c.parent.mkdir(parents=True)

    _write_skill(skill_a, "disk-space", "Check disk usage")
    _write_skill(skill_b, "system-status", "Show system status")
    _write_skill(skill_c, "create-skill", "Guide for creating skills")

    tree = tree_skills_cmd({"skills_dir": str(skills_dir), "plain": True})

    assert tree["server-management"] == ["disk-space", "system-status"]
    assert tree["meta"] == ["create-skill"]


def test_every_builtin_skill_file_is_named_exactly_skill_md() -> None:
    """Discovery globs `SKILL.md`, which is case-sensitive on Linux: a `skill.md` loads on
    Windows and silently vanishes everywhere else (win-perf-tuner shipped that way)."""
    import os

    import navig

    root = Path(navig.__file__).parent / "builtin" / "skills"
    assert root.is_dir()
    seen = 0
    wrong: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.lower() == "skill.md":
                seen += 1
                if name != "SKILL.md":
                    wrong.append(str(Path(dirpath, name).relative_to(root)))
    assert seen > 0
    assert not wrong, f"rename to SKILL.md: {wrong}"
