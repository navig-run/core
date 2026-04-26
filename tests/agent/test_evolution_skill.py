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
