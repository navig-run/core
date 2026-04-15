"""Tests for navig.commands.plan_mode — CLI command surface."""

from __future__ import annotations

from pathlib import Path
import pytest

# Use the Typer test client
from typer.testing import CliRunner
from navig.commands.plan_mode import app

runner = CliRunner()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write_plan(directory: Path, slug: str, goal: str, status: str = "ready") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    content = (
        f"---\nslug: {slug}\ngoal: \"{goal}\"\nstatus: {status}\ncreated_at: 2025-01-01T00:00:00+00:00\n---\n\n"
        f"# Plan: {goal}\n\nOverview here.\n"
    )
    path = directory / f"{slug}.md"
    path.write_text(content, encoding="utf-8")
    return path


# ─────────────────────────────────────────────────────────────────────────────
# navig plan list
# ─────────────────────────────────────────────────────────────────────────────

class TestPlanList:
    def test_no_plans_shows_hint(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "navig plan new" in result.output

    def test_lists_plans_from_navig_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / ".navig" / "plans"
        _write_plan(plan_dir, slug="20250101-add-auth", goal="Add authentication")
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "add-auth" in result.output

    def test_json_output(self, tmp_path, monkeypatch):
        import json

        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / ".navig" / "plans"
        _write_plan(plan_dir, slug="20250101-fix-bug", goal="Fix bug")
        result = runner.invoke(app, ["list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["goal"] == "Fix bug"

    def test_status_filter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / ".navig" / "plans"
        _write_plan(plan_dir, slug="20250101-a", goal="Goal A", status="done")
        _write_plan(plan_dir, slug="20250101-b", goal="Goal B", status="draft")
        result = runner.invoke(app, ["list", "--status", "done"])
        assert result.exit_code == 0
        assert "Goal A" in result.output
        # Should not show draft plan
        assert "Goal B" not in result.output


# ─────────────────────────────────────────────────────────────────────────────
# navig plan show
# ─────────────────────────────────────────────────────────────────────────────

class TestPlanShow:
    def test_shows_unknown_plan_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["show", "nonexistent-plan"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_shows_plan_raw(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / ".navig" / "plans"
        _write_plan(plan_dir, slug="20250101-my-plan", goal="My plan goal")
        result = runner.invoke(app, ["show", "20250101-my-plan", "--raw"])
        assert result.exit_code == 0
        assert "My plan goal" in result.output


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: _slugify, _extract_frontmatter
# ─────────────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_slugify_basic(self):
        from navig.commands.plan_mode import _slugify

        assert _slugify("Add user authentication to the API") == "add-user-authentication-to-the-api"

    def test_slugify_strips_special_chars(self):
        from navig.commands.plan_mode import _slugify

        assert _slugify("Fix bug #123 (critical)") == "fix-bug-123-critical"

    def test_slugify_truncates(self):
        from navig.commands.plan_mode import _slugify

        long_goal = "a " * 60
        slug = _slugify(long_goal)
        assert len(slug) <= 60

    def test_extract_frontmatter_parses_keys(self):
        from navig.commands.plan_mode import _extract_frontmatter

        content = "---\nslug: my-slug\nstatus: ready\n---\n\nBody here."
        meta = _extract_frontmatter(content)
        assert meta["slug"] == "my-slug"
        assert meta["status"] == "ready"

    def test_extract_frontmatter_empty_when_no_block(self):
        from navig.commands.plan_mode import _extract_frontmatter

        assert _extract_frontmatter("# No frontmatter\n\nBody.") == {}
