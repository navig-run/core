"""Batch 101: tests for navig.core.evolution.pack, .skill, .workflow."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# navig.core.evolution.pack
# ---------------------------------------------------------------------------

from navig.core.evolution.pack import PackEvolver


class TestPackEvolver:
    def test_evolve_success_mock_ai(self, tmp_path):
        evolver = PackEvolver()
        evolver._packs_dir = tmp_path
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            result = evolver.evolve("devops tools")
        assert result.success
        assert result.artifact is not None

    def test_validate_valid_yaml(self):
        evolver = PackEvolver()
        evolver._packs_dir = Path(".")
        artifact = "name: my_pack\nskills:\n  - deploy\n"
        error = evolver._validate(artifact, None)
        assert error is None

    def test_validate_missing_name(self):
        evolver = PackEvolver()
        evolver._packs_dir = Path(".")
        artifact = "skills:\n  - deploy\n"
        error = evolver._validate(artifact, None)
        assert error is not None
        assert "name" in error.lower()

    def test_validate_missing_skills_and_workflows(self):
        evolver = PackEvolver()
        evolver._packs_dir = Path(".")
        artifact = "name: empty_pack\ndescription: nothing here\n"
        error = evolver._validate(artifact, None)
        assert error is not None

    def test_validate_invalid_yaml(self):
        evolver = PackEvolver()
        evolver._packs_dir = Path(".")
        error = evolver._validate("key: [\n", None)
        assert error is not None

    def test_validate_non_dict_yaml(self):
        evolver = PackEvolver()
        evolver._packs_dir = Path(".")
        error = evolver._validate("- item1\n- item2\n", None)
        assert error is not None

    def test_save_creates_pack_yaml(self, tmp_path):
        evolver = PackEvolver()
        evolver._packs_dir = tmp_path
        artifact = "name: alpha_pack\ndescription: test\nskills:\n  - skill_a\n"
        evolver._save("create alpha pack", artifact)
        pack_file = tmp_path / "alpha_pack" / "pack.yaml"
        assert pack_file.exists()
        data = yaml.safe_load(pack_file.read_text())
        assert data["name"] == "alpha_pack"

    def test_generate_returns_mock_artifact(self, tmp_path):
        evolver = PackEvolver()
        evolver._packs_dir = tmp_path
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            artifact = evolver._generate("devops", None, "", None)
        assert "mock_pack" in artifact or "name:" in artifact

    def test_generate_with_previous_artifact(self, tmp_path):
        evolver = PackEvolver()
        evolver._packs_dir = tmp_path
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            artifact = evolver._generate("devops", "previous", "some error", None)
        assert artifact is not None


# ---------------------------------------------------------------------------
# navig.core.evolution.skill
# ---------------------------------------------------------------------------

from navig.core.evolution.skill import SkillEvolver


class TestSkillEvolver:
    def test_evolve_success_mock_ai(self, tmp_path):
        skills_root = tmp_path / "skills"
        skills_root.mkdir()
        evolver = SkillEvolver(skills_root)
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            result = evolver.evolve("deploy a docker container")
        assert result.success

    def test_validate_valid_frontmatter(self):
        skills_root = Path(".")
        evolver = SkillEvolver(skills_root)
        artifact = "---\nname: my_skill\ndescription: does stuff\n---\n# Instructions\nDo the thing carefully now.\n"
        error = evolver._validate(artifact, None)
        assert error is None

    def test_validate_missing_frontmatter(self):
        evolver = SkillEvolver(Path("."))
        artifact = "# Instructions\nJust do it.\n"
        error = evolver._validate(artifact, None)
        assert error is not None
        assert "frontmatter" in error.lower() or "---" in error

    def test_validate_missing_name(self):
        evolver = SkillEvolver(Path("."))
        artifact = "---\ndescription: skill without name\n---\n# Instructions\nDo a thing.\n"
        error = evolver._validate(artifact, None)
        assert error is not None
        assert "name" in error.lower()

    def test_validate_missing_description(self):
        evolver = SkillEvolver(Path("."))
        artifact = "---\nname: orphan_skill\n---\n# Instructions\nDo a thing.\n"
        error = evolver._validate(artifact, None)
        assert error is not None
        assert "description" in error.lower()

    def test_validate_instructions_too_short(self):
        evolver = SkillEvolver(Path("."))
        artifact = "---\nname: s\ndescription: d\n---\nshort\n"
        error = evolver._validate(artifact, None)
        assert error is not None

    def test_save_creates_skill_file(self, tmp_path):
        skills_root = tmp_path
        evolver = SkillEvolver(skills_root)
        artifact = "---\nname: deploy_skill\ndescription: deploys things\n---\n# Instructions\nRun the deploy command now.\n"
        evolver._save("deploy on server", artifact)
        skill_file = skills_root / "deploy_skill" / "SKILL.md"
        assert skill_file.exists()
        assert "deploy_skill" in skill_file.read_text()

    def test_save_sanitizes_skill_name(self, tmp_path):
        evolver = SkillEvolver(tmp_path)
        artifact = "---\nname: My Skill With Spaces!\ndescription: test skill\n---\n# Instructions\nDo the thing right now.\n"
        evolver._save("test", artifact)
        # Special chars in name should be replaced with "_"
        dirs = list(tmp_path.iterdir())
        assert len(dirs) == 1
        assert " " not in dirs[0].name
        assert "!" not in dirs[0].name

    def test_generate_mock_returns_frontmatter(self):
        evolver = SkillEvolver(Path("."))
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            artifact = evolver._generate("deploy", None, "", None)
        assert "---" in artifact
        assert "name:" in artifact


# ---------------------------------------------------------------------------
# navig.core.evolution.workflow
# ---------------------------------------------------------------------------

from navig.core.evolution.workflow import WorkflowEvolver


class TestWorkflowEvolver:
    def test_evolve_success_mock_ai(self, tmp_path):
        evolver = WorkflowEvolver()
        evolver._workflows_dir = tmp_path
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            result = evolver.evolve("open Calculator and take a screenshot")
        assert result.success
        assert result.artifact is not None

    def test_validate_valid_workflow(self):
        evolver = WorkflowEvolver()
        artifact = "name: test_wf\nsteps:\n  - action: wait\n    args:\n      seconds: 1\n"
        error = evolver._validate(artifact, None)
        assert error is None

    def test_validate_missing_steps(self):
        evolver = WorkflowEvolver()
        artifact = "name: no_steps\ndescription: missing steps\n"
        error = evolver._validate(artifact, None)
        assert error is not None
        assert "steps" in error.lower()

    def test_validate_steps_not_a_list(self):
        evolver = WorkflowEvolver()
        artifact = "name: bad\nsteps: not_a_list\n"
        error = evolver._validate(artifact, None)
        assert error is not None

    def test_validate_non_dict_root(self):
        evolver = WorkflowEvolver()
        error = evolver._validate("- item1\n- item2\n", None)
        assert error is not None

    def test_validate_invalid_yaml(self):
        evolver = WorkflowEvolver()
        error = evolver._validate("steps: [\n", None)
        assert error is not None

    def test_generate_mock_returns_yaml(self):
        evolver = WorkflowEvolver()
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            artifact = evolver._generate("open app", None, "", None)
        # Mock generates inline YAML (not in a code block)
        assert "name:" in artifact or "steps:" in artifact

    def test_generate_with_previous_artifact(self):
        evolver = WorkflowEvolver()
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            artifact = evolver._generate("open app", "prev", "some error", None)
        assert artifact is not None
