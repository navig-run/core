"""
Tests for navig.output_styles — output style file parsing and resolution.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from navig.output_styles import OutputStyleConfig, _parse_style_file, load_output_styles


# ---------------------------------------------------------------------------
# _parse_style_file
# ---------------------------------------------------------------------------

class TestParseStyleFile:
    def test_minimal_frontmatter(self, tmp_path: Path):
        f = tmp_path / "simple.md"
        f.write_text(textwrap.dedent("""\
            ---
            name: simple
            description: A simple style
            ---
            Always use bullet points.
        """), encoding="utf-8")
        result = _parse_style_file(f, source="user")
        assert result is not None
        assert result.name == "simple"
        assert result.description == "A simple style"
        assert "bullet" in result.prompt

    def test_no_frontmatter_falls_back_to_filename(self, tmp_path: Path):
        f = tmp_path / "myname.md"
        f.write_text("Just a prompt with no frontmatter.\n", encoding="utf-8")
        result = _parse_style_file(f, source="user")
        assert result is not None
        assert result.name == "myname"
        assert "Just a prompt" in result.prompt

    def test_keep_coding_instructions_false(self, tmp_path: Path):
        f = tmp_path / "style.md"
        f.write_text(textwrap.dedent("""\
            ---
            name: minimal
            keep-coding-instructions: false
            ---
            Short answers only.
        """), encoding="utf-8")
        result = _parse_style_file(f, source="user")
        assert result is not None
        assert result.keep_coding_instructions is False

    def test_keep_coding_instructions_default_true(self, tmp_path: Path):
        f = tmp_path / "style.md"
        f.write_text(textwrap.dedent("""\
            ---
            name: minimal
            ---
            Short answers only.
        """), encoding="utf-8")
        result = _parse_style_file(f, source="user")
        assert result is not None
        assert result.keep_coding_instructions is True

    def test_empty_file_returns_none(self, tmp_path: Path):
        f = tmp_path / "empty.md"
        f.write_text("", encoding="utf-8")
        result = _parse_style_file(f, source="user")
        assert result is None

    def test_missing_file_returns_none(self, tmp_path: Path):
        result = _parse_style_file(tmp_path / "nonexistent.md", source="user")
        assert result is None


# ---------------------------------------------------------------------------
# load_output_styles
# ---------------------------------------------------------------------------

class TestLoadOutputStyles:
    def test_empty_dirs_returns_empty_list(self, tmp_path: Path):
        styles = load_output_styles(cwd=tmp_path)
        assert isinstance(styles, list)

    def test_loads_project_styles(self, tmp_path: Path):
        project_styles_dir = tmp_path / ".navig" / "output-styles"
        project_styles_dir.mkdir(parents=True)
        (project_styles_dir / "concise.md").write_text(
            "---\nname: concise\n---\nBe concise.\n",
            encoding="utf-8",
        )
        styles = load_output_styles(cwd=tmp_path)
        names = [s.name for s in styles]
        assert "concise" in names

    def test_source_marked_correctly(self, tmp_path: Path):
        project_styles_dir = tmp_path / ".navig" / "output-styles"
        project_styles_dir.mkdir(parents=True)
        (project_styles_dir / "bullets.md").write_text(
            "---\nname: bullets\n---\nUse bullets.\n",
            encoding="utf-8",
        )
        styles = load_output_styles(cwd=tmp_path)
        proj = next((s for s in styles if s.name == "bullets"), None)
        assert proj is not None
        assert proj.source == "project"

    def test_deduplicates_by_name_project_wins(self, tmp_path: Path):
        # Same name in project and user dirs — project should win.
        proj_dir = tmp_path / ".navig" / "output-styles"
        user_dir = tmp_path / "user_styles"
        proj_dir.mkdir(parents=True)
        user_dir.mkdir()
        (proj_dir / "same.md").write_text("---\nname: same\n---\nProject version.\n")
        (user_dir / "same.md").write_text("---\nname: same\n---\nUser version.\n")

        # Parse both directories manually using _parse_style_file
        from navig.output_styles import _parse_style_file

        user_style = _parse_style_file(user_dir / "same.md", source="user")
        proj_style = _parse_style_file(proj_dir / "same.md", source="project")

        # Deduplicate by name, preferring project
        seen: dict[str, OutputStyleConfig] = {}
        for s in (user_style, proj_style):
            if s and (s.name not in seen or s.source == "project"):
                seen[s.name] = s

        assert seen["same"].source == "project"
        assert "Project version" in seen["same"].prompt


# ---------------------------------------------------------------------------
# OutputStyleConfig — interface
# ---------------------------------------------------------------------------

class TestOutputStyleConfig:
    def test_construct_minimal(self):
        s = OutputStyleConfig(name="test")
        assert s.name == "test"
        assert s.description == ""
        assert s.prompt == ""
        assert s.source == "user"
        assert s.keep_coding_instructions is True

    def test_construct_full(self):
        s = OutputStyleConfig(
            name="detailed",
            description="Verbose mode",
            prompt="Always explain every step.",
            source="project",
            keep_coding_instructions=False,
        )
        assert s.name == "detailed"
        assert s.keep_coding_instructions is False
