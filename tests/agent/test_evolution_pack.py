"""Tests for navig.core.evolution.pack.PackEvolver."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.core.evolution.pack import PackEvolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_YAML = """
name: test_pack
description: A test pack
version: "0.1.0"
skills:
  - test_skill
workflows:
  - test_workflow
"""

_YAML_WITH_FENCES = """
```yaml
name: fenced_pack
description: Fenced pack
skills:
  - skill_a
```
"""

_INVALID_YAML_NO_NAME = """
description: Missing name field
skills:
  - something
"""

_INVALID_YAML_NO_SKILLS = """
name: empty_pack
description: Has no skills or workflows
"""


class TestPackEvolverValidate:
    @pytest.fixture
    def evolver(self, tmp_path: Path) -> PackEvolver:
        with patch.object(Path, "mkdir"):  # avoid creating real dirs
            return PackEvolver()

    def test_valid_yaml_returns_none(self, evolver: PackEvolver) -> None:
        result = evolver._validate(_VALID_YAML, None)
        assert result is None

    def test_yaml_with_fences_returns_none(self, evolver: PackEvolver) -> None:
        result = evolver._validate(_YAML_WITH_FENCES, None)
        assert result is None

    def test_missing_name_returns_error(self, evolver: PackEvolver) -> None:
        result = evolver._validate(_INVALID_YAML_NO_NAME, None)
        assert result is not None
        assert "name" in result.lower()

    def test_missing_skills_and_workflows_returns_error(self, evolver: PackEvolver) -> None:
        result = evolver._validate(_INVALID_YAML_NO_SKILLS, None)
        assert result is not None

    def test_invalid_yaml_syntax_returns_error(self, evolver: PackEvolver) -> None:
        result = evolver._validate("this: is: not: valid: yaml: !!!", None)
        assert result is not None

    def test_non_dict_root_returns_error(self, evolver: PackEvolver) -> None:
        result = evolver._validate("- item1\n- item2\n", None)
        assert result is not None


class TestPackEvolverEvolveWithMockAI:
    @pytest.fixture
    def evolver(self, tmp_path: Path) -> PackEvolver:
        with patch.object(Path, "mkdir"):
            return PackEvolver()

    def test_evolve_succeeds_with_mock_ai(self, evolver: PackEvolver) -> None:
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            result = evolver.evolve("testing")
        assert result.success is True

    def test_evolve_returns_artifact_with_mock_ai(self, evolver: PackEvolver) -> None:
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            result = evolver.evolve("testing")
        assert result.artifact is not None

    def test_mock_artifact_contains_mock_pack(self, evolver: PackEvolver) -> None:
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            result = evolver.evolve("testing")
        assert "mock_pack" in result.artifact

    def test_attempts_is_positive(self, evolver: PackEvolver) -> None:
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            result = evolver.evolve("testing")
        assert result.attempts >= 1
