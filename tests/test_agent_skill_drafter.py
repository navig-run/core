"""Tests for navig.agent.skill_drafter — SkillDrafter, SkillDraft."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from navig.agent.skill_drafter import SkillDraft, SkillDrafter


@dataclass
class FakePattern:
    sequence: tuple[str, ...]


class TestSkillDraft:
    def test_construction(self):
        sd = SkillDraft(name="my-skill", safe=True, yaml_text="name: my-skill\n")
        assert sd.name == "my-skill"
        assert sd.safe is True


class TestSkillDrafter:
    def test_default_output_dir_set(self):
        drafter = SkillDrafter()
        assert drafter.output_dir is not None

    def test_custom_output_dir(self, tmp_path):
        drafter = SkillDrafter(output_dir=tmp_path)
        assert drafter.output_dir == tmp_path

    def test_draft_returns_skill_draft(self):
        drafter = SkillDrafter()
        pattern = FakePattern(sequence=("ls",))
        result = drafter.draft(pattern)
        assert isinstance(result, SkillDraft)

    def test_draft_name_slugified(self):
        drafter = SkillDrafter()
        pattern = FakePattern(sequence=("git status",))
        result = drafter.draft(pattern)
        assert result.name == "git-status"

    def test_draft_marks_unsafe_rm(self):
        drafter = SkillDrafter()
        pattern = FakePattern(sequence=("rm -rf /tmp/x",))
        result = drafter.draft(pattern)
        assert result.safe is False

    def test_draft_marks_unsafe_force(self):
        drafter = SkillDrafter()
        pattern = FakePattern(sequence=("git push --force",))
        result = drafter.draft(pattern)
        assert result.safe is False

    def test_draft_marks_safe_normal_command(self):
        drafter = SkillDrafter()
        pattern = FakePattern(sequence=("df -h",))
        result = drafter.draft(pattern)
        assert result.safe is True

    def test_draft_yaml_text_contains_name_and_command(self):
        drafter = SkillDrafter()
        pattern = FakePattern(sequence=("uptime",))
        result = drafter.draft(pattern)
        assert "name:" in result.yaml_text
        assert "uptime" in result.yaml_text

    def test_draft_empty_sequence_uses_fallback(self):
        drafter = SkillDrafter()
        pattern = FakePattern(sequence=())
        result = drafter.draft(pattern)
        assert result.name  # not empty
        assert "command" in result.yaml_text

    def test_slugify_replaces_special_chars(self):
        assert SkillDrafter._slugify("hello world!") == "hello-world"

    def test_slugify_truncates_at_64(self):
        long = "a" * 100
        assert len(SkillDrafter._slugify(long)) <= 64

    def test_apply_writes_file(self, tmp_path):
        drafter = SkillDrafter(output_dir=tmp_path)
        draft = SkillDraft(name="test-skill", safe=True, yaml_text="name: test-skill\n")
        path = drafter.apply(draft)
        assert path.exists()
        assert path.read_text() == "name: test-skill\n"

    def test_apply_creates_dir(self, tmp_path):
        out_dir = tmp_path / "skills" / "nested"
        drafter = SkillDrafter(output_dir=out_dir)
        draft = SkillDraft(name="s", safe=True, yaml_text="name: s\n")
        drafter.apply(draft)
        assert out_dir.exists()
