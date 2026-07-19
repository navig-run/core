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
        # Point the evolver AT tmp_path. The old fixture took `tmp_path` and never
        # used it — it only patched Path.mkdir for the duration of the constructor,
        # leaving _packs_dir as the CWD-relative "packs". See the sibling class:
        # that made `evolve()` write the TRACKED core/packs/mock_pack/pack.yaml.
        return PackEvolver(packs_dir=tmp_path)

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
        # `evolve()` runs the full loop through `_save()`, which writes
        # <packs_dir>/<name>/pack.yaml. The mock AI names its pack "mock_pack", so
        # with the old CWD-relative default this REWROTE the tracked, committed
        # core/packs/mock_pack/pack.yaml on every run — the suite dirtied the repo.
        return PackEvolver(packs_dir=tmp_path)

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


class TestPacksDir:
    def test_default_is_packages_dir_not_cwd(self, tmp_path, monkeypatch):
        """REGRESSION: the default was Path("packs") — CWD-relative — so a generated pack
        landed in whatever dir the process ran in and nothing loaded it. It must default to
        packages_dir() (config_dir()/packs), where `navig install` writes and the loader reads."""
        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        from navig.platform.paths import packages_dir

        assert PackEvolver()._packs_dir == packages_dir()
        assert PackEvolver()._packs_dir.is_absolute()

    def test_explicit_packs_dir_still_wins(self, tmp_path):
        assert PackEvolver(packs_dir=tmp_path)._packs_dir == tmp_path
