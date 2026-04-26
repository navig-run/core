"""Tests for navig.commands.prompts — prompts_app CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import typer
from typer.testing import CliRunner

from navig.commands.prompts import prompts_app

runner = CliRunner()


def _patch_dir(tmp_path: Path):
    """Context manager that patches _PROMPTS_DIR to tmp_path."""
    return patch("navig.commands.prompts._PROMPTS_DIR", tmp_path)


# ---------------------------------------------------------------------------
# App structure
# ---------------------------------------------------------------------------

class TestPromptsAppStructure:
    def test_prompts_app_is_typer(self):
        assert isinstance(prompts_app, typer.Typer)

    def test_list_subcommand_available(self):
        with _patch_dir(Path("/tmp")):
            result = runner.invoke(prompts_app, ["list", "--help"])
        assert result.exit_code == 0

    def test_show_subcommand_available(self):
        result = runner.invoke(prompts_app, ["show", "--help"])
        assert result.exit_code == 0

    def test_edit_subcommand_available(self):
        result = runner.invoke(prompts_app, ["edit", "--help"])
        assert result.exit_code == 0

    def test_remove_subcommand_available(self):
        result = runner.invoke(prompts_app, ["remove", "--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# prompts_list
# ---------------------------------------------------------------------------

class TestPromptsList:
    def test_empty_dir_shows_no_prompts_message(self, tmp_path):
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["list"])
        assert result.exit_code == 0
        assert "No prompts" in result.output

    def test_empty_dir_shows_path(self, tmp_path):
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["list"])
        # The path appears in the output (possibly wrapped by Rich)
        path_parts = [p for p in str(tmp_path).split("\\") if len(p) >= 4]
        assert any(part in result.output for part in path_parts)

    def test_list_txt_file(self, tmp_path):
        (tmp_path / "my-prompt.txt").write_text("content")
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["list"])
        assert "my-prompt" in result.output

    def test_list_md_file(self, tmp_path):
        (tmp_path / "system.md").write_text("# System")
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["list"])
        assert "system" in result.output

    def test_list_multiple_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("A")
        (tmp_path / "b.txt").write_text("B")
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["list"])
        assert "a" in result.output
        assert "b" in result.output

    def test_creates_dir_if_missing(self, tmp_path):
        missing = tmp_path / "new" / "deep"
        with _patch_dir(missing):
            runner.invoke(prompts_app, ["list"])
        assert missing.exists()

    def test_non_txt_md_not_listed(self, tmp_path):
        (tmp_path / "config.yaml").write_text("yaml")
        (tmp_path / "my-prompt.txt").write_text("prompt")
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["list"])
        assert "config" not in result.output


# ---------------------------------------------------------------------------
# prompts_show
# ---------------------------------------------------------------------------

class TestPromptsShow:
    def test_show_existing_txt_file(self, tmp_path):
        (tmp_path / "greeting.txt").write_text("Hello from prompt!")
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["show", "greeting"])
        assert result.exit_code == 0
        assert "Hello from prompt!" in result.output

    def test_show_existing_md_file(self, tmp_path):
        (tmp_path / "system.md").write_text("# System prompt")
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["show", "system"])
        assert result.exit_code == 0
        assert "System prompt" in result.output

    def test_show_missing_file_exit_1(self, tmp_path):
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["show", "does-not-exist"])
        assert result.exit_code == 1

    def test_show_missing_shows_not_found(self, tmp_path):
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["show", "missing"])
        assert "not found" in result.output.lower() or "Prompt not found" in result.output

    def test_show_prefers_txt_over_md(self, tmp_path):
        (tmp_path / "dual.txt").write_text("from txt")
        (tmp_path / "dual.md").write_text("from md")
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["show", "dual"])
        assert "from txt" in result.output


# ---------------------------------------------------------------------------
# prompts_remove
# ---------------------------------------------------------------------------

class TestPromptsRemove:
    def test_remove_existing_txt(self, tmp_path):
        f = tmp_path / "old.txt"
        f.write_text("to delete")
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["remove", "old"])
        assert result.exit_code == 0
        assert not f.exists()

    def test_remove_existing_md(self, tmp_path):
        f = tmp_path / "system.md"
        f.write_text("sys")
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["remove", "system"])
        assert not f.exists()

    def test_remove_shows_deleted_message(self, tmp_path):
        (tmp_path / "p.txt").write_text("x")
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["remove", "p"])
        assert "Deleted" in result.output or "deleted" in result.output.lower()

    def test_remove_missing_shows_not_found(self, tmp_path):
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["remove", "ghost"])
        assert result.exit_code == 0
        assert "Not found" in result.output or "not found" in result.output.lower()

    def test_remove_does_not_raise_on_missing(self, tmp_path):
        with _patch_dir(tmp_path):
            result = runner.invoke(prompts_app, ["remove", "never-created"])
        assert result.exit_code == 0
