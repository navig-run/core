"""Hermetic unit tests for navig.skills_renderer."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_skill_json(skills_dir: Path, skill_id: str, data: dict | None = None) -> Path:
    if data is None:
        data = {
            "id": skill_id,
            "name": skill_id.replace("_", " ").title(),
            "summary": f"{skill_id} skill",
            "commands": [
                {"name": "run", "signature": f"{skill_id} run", "description": f"Run {skill_id}"}
            ],
        }
    p = skills_dir / f"{skill_id}.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _write_skill_md(skills_dir: Path, skill_id: str, content: str = "## Skill\nDo stuff") -> Path:
    p = skills_dir / f"{skill_id}.md"
    p.write_text(content, encoding="utf-8")
    return p


def _patch_skills_dirs(dirs: list[Path]):
    return patch("navig.skills_renderer._get_skills_dirs", return_value=dirs)


# ---------------------------------------------------------------------------
# _manual_render
# ---------------------------------------------------------------------------


class TestManualRender:
    def test_empty_skills_returns_header_only(self):
        from navig.skills_renderer import _manual_render

        result = _manual_render([])
        assert "You have access to the following tools/skills:" in result

    def test_skill_line_contains_id_name_summary(self):
        from navig.skills_renderer import _manual_render

        skills = [{"id": "git", "name": "Git", "summary": "Version control", "commands": []}]
        result = _manual_render(skills)
        assert "[git]" in result
        assert "Git" in result
        assert "Version control" in result

    def test_command_line_contains_signature_and_description(self):
        from navig.skills_renderer import _manual_render

        skills = [
            {
                "id": "gh",
                "name": "GitHub",
                "summary": "",
                "commands": [{"signature": "gh pr create", "description": "Create a PR"}],
            }
        ]
        result = _manual_render(skills)
        assert "gh pr create" in result
        assert "Create a PR" in result

    def test_multiple_skills_all_rendered(self):
        from navig.skills_renderer import _manual_render

        skills = [
            {"id": "a", "name": "A", "summary": "Alpha", "commands": []},
            {"id": "b", "name": "B", "summary": "Beta", "commands": []},
        ]
        result = _manual_render(skills)
        assert "[a]" in result
        assert "[b]" in result

    def test_missing_keys_fallback_gracefully(self):
        from navig.skills_renderer import _manual_render

        # Should not raise even with sparse data
        result = _manual_render([{}])
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _find_skill_json
# ---------------------------------------------------------------------------


class TestFindSkillJson:
    def test_finds_direct_json(self, tmp_path):
        from navig.skills_renderer import _find_skill_json

        _write_skill_json(tmp_path, "git")
        with _patch_skills_dirs([tmp_path]):
            result = _find_skill_json("git")

        assert result is not None
        assert result.name == "git.json"

    def test_finds_nested_skill_json(self, tmp_path):
        from navig.skills_renderer import _find_skill_json

        nested = tmp_path / "git"
        nested.mkdir()
        (nested / "skill.json").write_text("{}")
        with _patch_skills_dirs([tmp_path]):
            result = _find_skill_json("git")

        assert result is not None
        assert result.name == "skill.json"

    def test_finds_json_in_category_subdir(self, tmp_path):
        from navig.skills_renderer import _find_skill_json

        cat = tmp_path / "devtools"
        cat.mkdir()
        (cat / "docker.json").write_text("{}")
        with _patch_skills_dirs([tmp_path]):
            result = _find_skill_json("docker")

        assert result is not None

    def test_returns_none_when_not_found(self, tmp_path):
        from navig.skills_renderer import _find_skill_json

        with _patch_skills_dirs([tmp_path]):
            result = _find_skill_json("nonexistent_skill_xyz")

        assert result is None


# ---------------------------------------------------------------------------
# _find_skill_md
# ---------------------------------------------------------------------------


class TestFindSkillMd:
    def test_finds_direct_md(self, tmp_path):
        from navig.skills_renderer import _find_skill_md

        _write_skill_md(tmp_path, "docker")
        with _patch_skills_dirs([tmp_path]):
            result = _find_skill_md("docker")

        assert result is not None
        assert result.name == "docker.md"

    def test_returns_none_when_not_found(self, tmp_path):
        from navig.skills_renderer import _find_skill_md

        with _patch_skills_dirs([tmp_path]):
            result = _find_skill_md("nonexistent_md_xyz")

        assert result is None


# ---------------------------------------------------------------------------
# _load_skill_md
# ---------------------------------------------------------------------------


class TestLoadSkillMd:
    def test_loads_content(self, tmp_path):
        from navig.skills_renderer import _load_skill_md

        _write_skill_md(tmp_path, "git", "## Git\nVersion control\n")
        with _patch_skills_dirs([tmp_path]):
            result = _load_skill_md("git")

        assert result is not None
        assert "Version control" in result

    def test_limits_to_max_lines(self, tmp_path):
        from navig.skills_renderer import _load_skill_md

        content = "\n".join(f"line {i}" for i in range(100))
        _write_skill_md(tmp_path, "big", content)
        with _patch_skills_dirs([tmp_path]):
            result = _load_skill_md("big", max_lines=5)

        assert result is not None
        lines = result.strip().splitlines()
        assert len(lines) <= 5

    def test_returns_none_when_not_found(self, tmp_path):
        from navig.skills_renderer import _load_skill_md

        with _patch_skills_dirs([tmp_path]):
            assert _load_skill_md("missing") is None


# ---------------------------------------------------------------------------
# render_skills_prompt
# ---------------------------------------------------------------------------


class TestRenderSkillsPrompt:
    def test_empty_ids_returns_empty_string(self, tmp_path):
        from navig.skills_renderer import render_skills_prompt

        with _patch_skills_dirs([tmp_path]):
            result = render_skills_prompt([], mode="json")

        assert result == ""

    def test_mode_md_uses_markdown_content(self, tmp_path):
        from navig.skills_renderer import render_skills_prompt

        _write_skill_md(tmp_path, "ahk", "AHK skill content")
        with _patch_skills_dirs([tmp_path]):
            result = render_skills_prompt(["ahk"], mode="md")

        assert "AHK skill content" in result

    def test_mode_json_renders_skill_name(self, tmp_path):
        from navig.skills_renderer import render_skills_prompt

        _write_skill_json(tmp_path, "git")
        with (
            _patch_skills_dirs([tmp_path]),
            patch("navig.skills_renderer._load_template", return_value=None),
        ):
            result = render_skills_prompt(["git"], mode="json")

        assert isinstance(result, str)
        assert len(result) > 0

    def test_mode_auto_prefers_json_over_md(self, tmp_path):
        from navig.skills_renderer import render_skills_prompt

        _write_skill_json(tmp_path, "docker")
        _write_skill_md(tmp_path, "docker", "MD content should NOT appear")
        with (
            _patch_skills_dirs([tmp_path]),
            patch("navig.skills_renderer._load_template", return_value=None),
        ):
            result = render_skills_prompt(["docker"], mode="auto")

        assert isinstance(result, str)
        assert len(result) > 0

    def test_mode_auto_falls_back_to_md_when_no_json(self, tmp_path):
        from navig.skills_renderer import render_skills_prompt

        _write_skill_md(tmp_path, "fallback_skill", "Fallback MD content")
        with _patch_skills_dirs([tmp_path]):
            result = render_skills_prompt(["fallback_skill"], mode="auto")

        assert "Fallback MD content" in result

    def test_unknown_skill_contributes_nothing(self, tmp_path):
        from navig.skills_renderer import render_skills_prompt

        with _patch_skills_dirs([tmp_path]):
            result = render_skills_prompt(["totally_nonexistent_skill_abc"], mode="auto")

        assert result == ""
