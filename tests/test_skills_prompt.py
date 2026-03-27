"""Tests for Skills prompt renderer."""

import json

import pytest


class TestSkillsPrompt:
    def test_json_manifest_renders(self, tmp_path):
        """JSON skill manifest renders correctly via template."""
        from navig.skills_renderer import _manual_render

        # Create a skill JSON
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "test_skill.json").write_text(
            json.dumps(
                {
                    "id": "test_skill",
                    "name": "Test Skill",
                    "summary": "A test skill for unit tests.",
                    "commands": [
                        {
                            "name": "do_thing",
                            "signature": "do_thing(arg1, arg2)",
                            "description": "Does a thing with two arguments.",
                        },
                    ],
                }
            )
        )

        # Use manual render (no Jinja2 dependency needed)
        data = json.loads((skill_dir / "test_skill.json").read_text())
        result = _manual_render([data])

        assert "test_skill" in result
        assert "Test Skill" in result
        assert "do_thing(arg1, arg2)" in result
        assert "Does a thing" in result

    def test_md_fallback(self, tmp_path):
        """mode='auto' falls back to .md when JSON is missing."""
        from navig.skills_renderer import _load_skill_md

        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        md_content = (
            "# My Skill\n\nThis is a skill description.\n"
            + "Line {}\n".format("x") * 60
        )
        (skill_dir / "md_skill.md").write_text(md_content)

        # Load first 50 lines
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("navig.skills_renderer._get_skills_dirs", lambda: [skill_dir])
            result = _load_skill_md("md_skill", max_lines=50)

        assert result is not None
        assert "# My Skill" in result
        # Should be at most 50 lines
        assert result.count("\n") <= 50

    def test_auto_mode_prefers_json(self, tmp_path):
        """auto mode uses JSON when available, even if .md also exists."""
        from navig.skills_renderer import render_skills_prompt

        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()

        # Create both JSON and MD
        (skill_dir / "dual.json").write_text(
            json.dumps(
                {
                    "id": "dual",
                    "name": "Dual Skill",
                    "summary": "From JSON manifest.",
                    "commands": [],
                }
            )
        )
        (skill_dir / "dual.md").write_text("# Dual\nFrom markdown file.")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("navig.skills_renderer._get_skills_dirs", lambda: [skill_dir])
            mp.setattr("navig.skills_renderer._get_context_skills_mode", lambda: "auto")
            result = render_skills_prompt(["dual"])

        assert "From JSON manifest" in result
        assert "From markdown file" not in result

    def test_empty_skill_ids(self):
        """Empty skill list returns empty string."""
        from navig.skills_renderer import render_skills_prompt

        result = render_skills_prompt([])
        assert result == ""

    def test_missing_skill(self, tmp_path):
        """Missing skill is silently skipped."""
        from navig.skills_renderer import render_skills_prompt

        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("navig.skills_renderer._get_skills_dirs", lambda: [skill_dir])
            result = render_skills_prompt(["nonexistent_skill"])

        assert result == ""

    def test_manual_render_multiple_skills(self):
        """Manual renderer handles multiple skills correctly."""
        from navig.skills_renderer import _manual_render

        skills = [
            {
                "id": "skill_a",
                "name": "Skill A",
                "summary": "First skill.",
                "commands": [
                    {"signature": "a_cmd()", "description": "Does A."},
                ],
            },
            {
                "id": "skill_b",
                "name": "Skill B",
                "summary": "Second skill.",
                "commands": [
                    {"signature": "b_cmd(x)", "description": "Does B."},
                    {"signature": "b_other(y)", "description": "Other B."},
                ],
            },
        ]
        result = _manual_render(skills)

        assert "[skill_a]" in result
        assert "[skill_b]" in result
        assert "a_cmd()" in result
        assert "b_cmd(x)" in result
        assert "b_other(y)" in result
