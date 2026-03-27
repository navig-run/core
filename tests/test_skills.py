"""Tests for skills command helpers."""

from pathlib import Path

from navig.commands.skills import list_skills_cmd, tree_skills_cmd


def _write_skill(path: Path, name: str, description: str) -> None:
    content = """---
name: {name}
description: {description}
---

# {title}
""".format(
        name=name, description=description, title=name.replace("-", " ").title()
    )
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
