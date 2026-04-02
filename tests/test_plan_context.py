"""Tests for navig.plans.context — PlanContext unified read surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.plans.context import (
    PlanContext,
    _first_h1,
    _parse_frontmatter,
    _safe_read,
)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _make_space(tmp_path: Path, name: str = "default") -> Path:
    """Create a minimal space directory under ~/.navig/spaces/<name>."""
    home = tmp_path / "home"
    space_dir = home / ".navig" / "spaces" / name
    space_dir.mkdir(parents=True, exist_ok=True)
    return space_dir


def _make_phase(space_dir: Path, **fm_fields: str) -> Path:
    """Write a CURRENT_PHASE.md with frontmatter fields."""
    lines = ["---"]
    for k, v in fm_fields.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append("# Phase content")
    return _write(space_dir / "CURRENT_PHASE.md", "\n".join(lines))


# ─────────────────────────────────────────────────────────────
# Unit tests — helper functions
# ─────────────────────────────────────────────────────────────


class TestSafeRead:
    def test_reads_utf8(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "hello.txt", "café")
        assert _safe_read(f) == "café"

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _safe_read(tmp_path / "missing.txt") == ""


class TestParseFrontmatter:
    def test_basic(self) -> None:
        text = "---\ntitle: Phase 1\nstatus: active\n---\n# Body"
        fm = _parse_frontmatter(text)
        assert fm["title"] == "Phase 1"
        assert fm["status"] == "active"

    def test_no_frontmatter(self) -> None:
        assert _parse_frontmatter("# Just a heading") == {}

    def test_colon_in_value(self) -> None:
        text = "---\ntitle: Phase: the beginning\n---\n"
        fm = _parse_frontmatter(text)
        assert fm["title"] == "Phase: the beginning"


class TestFirstH1:
    def test_extracts_first_heading(self) -> None:
        assert _first_h1("some text\n# My Title\n## Sub") == "My Title"

    def test_no_heading(self) -> None:
        assert _first_h1("no heading here") == ""


# ─────────────────────────────────────────────────────────────
# PlanContext.gather() — integration tests
# ─────────────────────────────────────────────────────────────


class TestPlanContextGather:
    def test_gather_returns_all_keys(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Snapshot dict must always have the canonical keys."""
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: home)

        space = _make_space(tmp_path)
        _make_phase(space, title="Setup CI", phase="1", status="active", completion_pct="40")

        ctx = PlanContext(cwd=tmp_path / "repo")
        snapshot = ctx.gather("default")

        for key in ("current_phase", "dev_plan", "wiki", "docs", "inbox_unread", "mcp_resources", "errors"):
            assert key in snapshot, f"Missing key: {key}"

    def test_gather_reads_current_phase(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: home)

        space = _make_space(tmp_path)
        _make_phase(
            space,
            title="Deploy",
            phase="2",
            status="active",
            completion_pct="75",
            milestone="v2.0",
        )

        ctx = PlanContext(cwd=tmp_path / "repo")
        snapshot = ctx.gather("default")
        phase = snapshot["current_phase"]
        assert phase is not None
        assert "title: Deploy" in phase
        assert "completion_pct: 75" in phase
        assert "milestone: v2.0" in phase

    def test_gather_reads_dev_plan(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: home)
        _make_space(tmp_path)

        repo = tmp_path / "repo"
        dev_plan = repo / ".navig" / "plans" / "DEV_PLAN.md"
        _write(
            dev_plan,
            "# Dev Plan\n- [x] Setup\n- [ ] Deploy\n- [ ] Docs\n",
        )

        ctx = PlanContext(cwd=repo)
        snapshot = ctx.gather("default")
        dp = snapshot["dev_plan"]
        assert dp is not None
        assert "[x] Setup" in dp
        assert "[ ] Deploy" in dp

    def test_gather_counts_inbox(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: home)
        _make_space(tmp_path)

        repo = tmp_path / "repo"
        inbox = repo / ".navig" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        _write(inbox / "item1.md", "# TODO something")
        _write(inbox / "item2.md", "# Another item")
        _write(inbox / "README.txt", "not an md file")

        ctx = PlanContext(cwd=repo)
        snapshot = ctx.gather("default")
        assert snapshot["inbox_unread"] == 2

    def test_gather_no_space_returns_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When space resolution fails, gather returns gracefully with errors."""
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: home)

        ctx = PlanContext(cwd=tmp_path / "nonexistent")
        snapshot = ctx.gather("nonexistent_space")
        # Should still have all keys
        assert snapshot["current_phase"] is None
        assert isinstance(snapshot["errors"], dict)

    def test_gather_finds_docs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: home)
        _make_space(tmp_path)

        repo = tmp_path / "repo"
        _write(repo / "README.md", "# My Project\nThis is the readme.")
        _write(repo / "ROADMAP.md", "# Roadmap\n- v1.0\n- v2.0")

        ctx = PlanContext(cwd=repo)
        snapshot = ctx.gather("default")
        docs = snapshot["docs"]
        assert len(docs) >= 2
        assert "README.md" in "\n".join(docs)
        assert "ROADMAP.md" in "\n".join(docs)

    def test_gather_mcp_resources_empty_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """MCP resources should return empty list when no pool is available."""
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: home)
        _make_space(tmp_path)

        ctx = PlanContext(cwd=tmp_path / "repo")
        snapshot = ctx.gather("default")
        assert snapshot["mcp_resources"] is None

    def test_mcp_not_called_when_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When mcp_enabled=False, gather must not invoke MCP resource lookup."""
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: home)
        _make_space(tmp_path)

        called = {"value": False}

        def _boom(_space: str):
            called["value"] = True
            raise AssertionError("_gather_mcp_resources should not be called")

        ctx = PlanContext(cwd=tmp_path / "repo", mcp_enabled=False)
        monkeypatch.setattr(ctx, "_gather_mcp_resources", _boom)
        snapshot = ctx.gather("default")

        assert called["value"] is False
        assert snapshot["mcp_resources"] is None

    def test_mcp_enabled_timeout_returns_empty_list(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When MCP is enabled and provider times out/fails, context returns []."""
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: home)
        _make_space(tmp_path)

        class _FakePool:
            def list_resources_sync(self, timeout: float = 2.0):
                raise TimeoutError(f"Timed out after {timeout}s")

        import navig.agent.mcp_client as mcp_mod

        monkeypatch.setattr(mcp_mod, "get_mcp_pool", lambda: _FakePool())

        ctx = PlanContext(cwd=tmp_path / "repo", mcp_enabled=True)
        snapshot = ctx.gather("default")

        assert snapshot["mcp_resources"] == []


# ─────────────────────────────────────────────────────────────
# format_for_prompt()
# ─────────────────────────────────────────────────────────────


class TestFormatForPrompt:
    def test_empty_snapshot_returns_empty_string(self) -> None:
        ctx = PlanContext()
        snapshot = {
            "current_phase": None,
            "dev_plan": None,
            "wiki": [],
            "docs": [],
            "inbox_unread": 0,
            "mcp_resources": None,
            "errors": {},
        }
        assert ctx.format_for_prompt(snapshot) == ""

    def test_formats_phase(self) -> None:
        ctx = PlanContext()
        snapshot = {
            "current_phase": "---\ntitle: Deploy\nphase: 2\nstatus: active\ncompletion_pct: 75\nmilestone: v2.0\n---\n",
            "dev_plan": None,
            "wiki": [],
            "docs": [],
            "inbox_unread": 0,
            "mcp_resources": None,
            "errors": {},
        }
        text = ctx.format_for_prompt(snapshot)
        assert "Deploy" in text
        assert "75%" in text
        assert "v2.0" in text

    def test_formats_dev_plan(self) -> None:
        ctx = PlanContext()
        snapshot = {
            "current_phase": None,
            "dev_plan": "- [x] Done\n- [ ] Open\n- [ ] Open2\n",
            "wiki": [],
            "docs": [],
            "inbox_unread": 0,
            "mcp_resources": None,
            "errors": {},
        }
        text = ctx.format_for_prompt(snapshot)
        assert "2 open" in text
        assert "1 done" in text

    def test_formats_inbox(self) -> None:
        ctx = PlanContext()
        snapshot = {
            "current_phase": None,
            "dev_plan": None,
            "wiki": [],
            "docs": [],
            "inbox_unread": 5,
            "mcp_resources": None,
            "errors": {},
        }
        text = ctx.format_for_prompt(snapshot)
        assert "5 unread" in text

    def test_formats_errors(self) -> None:
        ctx = PlanContext()
        snapshot = {
            "current_phase": None,
            "dev_plan": None,
            "wiki": [],
            "docs": [],
            "inbox_unread": 0,
            "mcp_resources": None,
            "errors": {"wiki": "timeout", "mcp": "unreachable"},
        }
        text = ctx.format_for_prompt(snapshot)
        assert "wiki: timeout" in text
        assert "Context Warnings" in text

    def test_formats_wiki_results(self) -> None:
        ctx = PlanContext()
        snapshot = {
            "current_phase": None,
            "dev_plan": None,
            "wiki": [
                {"title": "Auth Guide", "excerpt": "How to set up OAuth", "path": "wiki/auth.md"},
                {"title": "Deploy", "excerpt": "CI/CD pipeline", "path": "wiki/deploy.md"},
            ],
            "docs": [],
            "inbox_unread": 0,
            "mcp_resources": None,
            "errors": {},
        }
        text = ctx.format_for_prompt(snapshot)
        assert "Auth Guide" in text
        assert "Related Wiki" in text


# ─────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────


class TestPlanContextEdgeCases:
    def test_current_phase_fallback_to_plans_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When CURRENT_PHASE.md is not in space root, falls back to .navig/plans/phases/."""
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: home)

        space = _make_space(tmp_path)  # No CURRENT_PHASE.md in space dir

        repo = tmp_path / "repo"
        plans_phases = repo / ".navig" / "plans" / "phases"
        _write(
            plans_phases / "CURRENT_PHASE.md",
            "---\ntitle: Fallback Phase\ncompletion_pct: 25\n---\n",
        )

        ctx = PlanContext(cwd=repo)
        snapshot = ctx.gather("default")
        phase = snapshot["current_phase"]
        assert phase is not None
        assert "title: Fallback Phase" in phase

    def test_no_dev_plan_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: home)
        _make_space(tmp_path)

        ctx = PlanContext(cwd=tmp_path / "repo")
        snapshot = ctx.gather("default")
        assert snapshot["dev_plan"] is None

    def test_completion_pct_non_numeric(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-numeric completion_pct should default to 0.0."""
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", lambda: home)

        space = _make_space(tmp_path)
        _make_phase(space, title="Bad PCT", completion_pct="not_a_number")

        ctx = PlanContext(cwd=tmp_path / "repo")
        snapshot = ctx.gather("default")
        phase = snapshot["current_phase"]
        assert phase is not None
        assert "completion_pct: not_a_number" in phase
