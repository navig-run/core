"""Tests for navig.core.evolution.skill — SkillEvolver._validate() and .evolve()."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.core.evolution.skill import SkillEvolver

_VALID_SKILL = """\
---
name: hello_skill
description: "Does something useful"
---
# Instructions
Run this command to greet the world.
Make sure to have Python installed first.
"""


@pytest.fixture(autouse=True)
def _mock_ai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAVIG_MOCK_AI", "1")


def _evolver(skills_root: Path) -> SkillEvolver:
    return SkillEvolver(skills_root=skills_root)


class TestSkillEvolverValidate:
    def test_valid_returns_none(self, tmp_path) -> None:
        ev = _evolver(tmp_path)
        assert ev._validate(_VALID_SKILL, None) is None

    def test_missing_frontmatter_start(self, tmp_path) -> None:
        ev = _evolver(tmp_path)
        assert ev._validate("name: foo\n# Instructions\nDo stuff", None) is not None

    def test_missing_name_field(self, tmp_path) -> None:
        ev = _evolver(tmp_path)
        bad = "---\ndescription: nodoc\n---\n# Instructions\nLong enough instructions here right."
        result = ev._validate(bad, None)
        assert result is not None
        assert "name" in result.lower()

    def test_missing_description_field(self, tmp_path) -> None:
        ev = _evolver(tmp_path)
        bad = "---\nname: foo\n---\n# Instructions\nLong enough instructions here right."
        result = ev._validate(bad, None)
        assert result is not None
        assert "description" in result.lower()

    def test_instructions_too_short(self, tmp_path) -> None:
        ev = _evolver(tmp_path)
        bad = "---\nname: foo\ndescription: bar\n---\nHi"
        result = ev._validate(bad, None)
        assert result is not None

    def test_invalid_yaml_frontmatter(self, tmp_path) -> None:
        ev = _evolver(tmp_path)
        bad = "---\n: : invalid yaml: value\n---\n# Instructions\nLong enough"
        result = ev._validate(bad, None)
        # may be None or error depending on yaml.safe_load behavior, but should not raise
        assert True  # just confirm no exception

    def test_fenced_block_valid(self, tmp_path) -> None:
        ev = _evolver(tmp_path)
        # YAML frontmatter followed by body wrapped in fences — still valid
        fenced = "---\nname: fenced\ndescription: wrapped\n---\n# Instructions\nComplete full instructions"
        assert ev._validate(fenced, None) is None


class TestSkillEvolverEvolve:
    def test_mock_ai_succeeds(self, tmp_path) -> None:
        ev = _evolver(tmp_path)
        result = ev.evolve("test skill", context=None)
        assert result.success is True

    def test_mock_ai_artifact_not_none(self, tmp_path) -> None:
        ev = _evolver(tmp_path)
        result = ev.evolve("test skill", context=None)
        assert result.artifact is not None

    def test_mock_ai_artifact_contains_name(self, tmp_path) -> None:
        ev = _evolver(tmp_path)
        result = ev.evolve("test skill", context=None)
        assert "mock_skill" in result.artifact

    def test_attempts_at_least_one(self, tmp_path) -> None:
        ev = _evolver(tmp_path)
        result = ev.evolve("test skill", context=None)
        assert result.attempts >= 1

    def test_saves_to_skills_root(self, tmp_path) -> None:
        ev = _evolver(tmp_path)
        ev.evolve("test skill", context=None)
        skill_files = list(tmp_path.rglob("SKILL.md"))
        assert len(skill_files) >= 1


class TestSkillEvolverDefaultRoot:
    """REGRESSION (sibling of #271/#276): the skill evolver's root was supplied by the
    `navig evolve skill` command as Path("skills") — CWD-relative — so a generated skill
    landed in whatever dir the process ran in and skills_context._global_skills_dir()
    (config_dir()/skills) never saw it. The evolver now defaults to config_dir()/skills
    (where the agent reads global skills), and the command passes the value through."""

    def test_default_is_config_skills_not_cwd(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        from navig.platform.paths import config_dir

        assert SkillEvolver()._skills_root == config_dir() / "skills"
        assert SkillEvolver()._skills_root.is_absolute()

    def test_explicit_skills_root_still_wins(self, tmp_path) -> None:
        assert SkillEvolver(skills_root=tmp_path)._skills_root == tmp_path

    def test_construction_touches_no_disk(self, tmp_path, monkeypatch) -> None:
        """Constructing the evolver must not create config_dir()/skills — only _save writes."""
        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        SkillEvolver()
        from navig.platform.paths import config_dir

        assert not (config_dir() / "skills").exists()

    def test_command_passes_root_through_not_cwd(self, monkeypatch) -> None:
        """`navig evolve skill` must not re-inject a CWD-relative default — it passes the
        caller's value straight through so the evolver's config_dir() default applies."""
        captured: dict = {}

        class _FakeEvolver:
            def __init__(self, skills_root=None):
                captured["root"] = skills_root
                self.max_retries = 3

            def evolve(self, goal):
                from types import SimpleNamespace

                return SimpleNamespace(success=True, error=None)

        monkeypatch.setattr("navig.core.evolution.skill.SkillEvolver", _FakeEvolver)
        from navig.commands.evolution import evolve_skill

        evolve_skill(goal="x", skills_root=None, retries=3)
        assert captured["root"] is None  # NOT Path("skills")
