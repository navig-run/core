"""Tests for navig.agent.tools.repo_search_tools (file_search / grep_search).

Covers both search backends (ripgrep when present, isolated Python worker
otherwise), the git-aware corpus (untracked included, .gitignore honored),
workspace-escape refusal, bounds/truncation, binary/large-file skips, the
restricted regex policy, and the hard-deadline kill.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from navig.agent.tools import repo_search_tools as rst
from navig.agent.tools.repo_search_tools import (
    _MAX_REGEX_LEN,
    FileSearchTool,
    GrepSearchTool,
    _validate_regex,
)

_HAS_GIT = shutil.which("git") is not None
_HAS_RG = shutil.which("rg") is not None


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def no_rg(monkeypatch):
    """Force the pure-Python worker backend."""
    monkeypatch.setattr(rst.shutil, "which", lambda _name: None)


@pytest.fixture
def workspace(tmp_path, monkeypatch) -> Path:
    """Active workshop cwd = a populated tmp workspace."""
    (tmp_path / "alpha.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("alpha appears here\n", encoding="utf-8")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "beta.py").write_text("import os\n# alpha in a comment\n", encoding="utf-8")
    monkeypatch.setattr(rst, "_active_cwd", lambda: tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# _validate_regex (restricted pattern policy)
# ---------------------------------------------------------------------------


class TestRegexPolicy:
    def test_plain_pattern_ok(self):
        assert _validate_regex(r"def\s+foo") is None

    def test_nested_quantifier_rejected(self):
        err = _validate_regex(r"(a+)+$")
        assert err is not None
        assert "nested quantifier" in err

    def test_backreference_rejected(self):
        err = _validate_regex(r"(a)\1")
        assert err is not None
        assert "backreference" in err

    def test_overlong_rejected(self):
        err = _validate_regex("a" * (_MAX_REGEX_LEN + 1))
        assert err is not None and "shorten" in err

    def test_invalid_regex_rejected(self):
        err = _validate_regex("(unclosed")
        assert err is not None and "invalid regex" in err


# ---------------------------------------------------------------------------
# file_search
# ---------------------------------------------------------------------------


class TestFileSearch:
    def setup_method(self):
        self.tool = FileSearchTool()

    def test_name(self):
        assert self.tool.name == "file_search"

    def test_glob_matches_at_any_depth(self, workspace):
        result = _run(self.tool.run({"pattern": "*.py"}))
        assert result.success is True
        assert "alpha.py" in result.output
        assert "beta.py" in result.output
        assert not any(ln.endswith("notes.md") for ln in result.output.splitlines())

    def test_no_match_is_honest_success(self, workspace):
        result = _run(self.tool.run({"pattern": "*.nothing"}))
        assert result.success is True
        assert "No files matched" in result.output

    def test_missing_pattern_fails(self, workspace):
        result = _run(self.tool.run({}))
        assert result.success is False
        assert "pattern" in result.error

    def test_default_root_is_active_workspace(self, workspace):
        result = _run(self.tool.run({"pattern": "alpha.*"}))
        assert result.success is True
        assert "alpha.py" in result.output

    def test_relative_escape_refused(self, workspace, tmp_path):
        # a sibling directory outside the workspace
        outside = tmp_path.parent / "outside-ws"
        outside.mkdir(exist_ok=True)
        (outside / "secret.txt").write_text("x", encoding="utf-8")
        rel = "../" + outside.name
        result = _run(self.tool.run({"pattern": "*.txt", "path": rel}))
        assert result.success is False
        assert "escapes the active workspace" in result.error

    def test_not_a_directory_fails(self, workspace):
        result = _run(self.tool.run({"pattern": "*.py", "path": "alpha.py"}))
        assert result.success is False
        assert "not a directory" in result.error

    def test_limit_truncates_honestly(self, workspace):
        for i in range(5):
            (workspace / f"bulk{i}.dat").write_text("x", encoding="utf-8")
        result = _run(self.tool.run({"pattern": "*.dat", "limit": 3}))
        assert result.success is True
        assert "match(es)" in result.output
        assert "raise 'limit'" in result.output
        found = [ln for ln in result.output.splitlines() if ln.endswith(".dat")]
        assert len(found) == 3

    @pytest.mark.skipif(not _HAS_GIT, reason="git not available")
    def test_git_corpus_includes_untracked_and_honors_ignore(self, tmp_path, monkeypatch):
        (tmp_path / "tracked_src.py").write_text("a = 1\n", encoding="utf-8")
        (tmp_path / "untracked_src.py").write_text("b = 2\n", encoding="utf-8")
        (tmp_path / "ignored_src.py").write_text("c = 3\n", encoding="utf-8")
        (tmp_path / ".gitignore").write_text("ignored_src.py\n", encoding="utf-8")
        subprocess.run(
            ["git", "init", "-q"], cwd=str(tmp_path), check=False, capture_output=True
        )
        subprocess.run(
            ["git", "add", "tracked_src.py"],
            cwd=str(tmp_path),
            check=False,
            capture_output=True,
        )
        monkeypatch.setattr(rst, "_active_cwd", lambda: tmp_path)

        result = _run(FileSearchTool().run({"pattern": "*_src.py"}))
        assert result.success is True
        assert "untracked_src.py" in result.output, "untracked sources must be findable"
        assert "tracked_src.py" in result.output
        assert "ignored_src.py" not in result.output
        assert "git ls-files" in result.output

        loose = _run(
            FileSearchTool().run({"pattern": "*_src.py", "respect_gitignore": False})
        )
        assert loose.success is True
        assert "ignored_src.py" in loose.output


# ---------------------------------------------------------------------------
# grep_search
# ---------------------------------------------------------------------------


class TestGrepSearch:
    def setup_method(self):
        self.tool = GrepSearchTool()

    def test_name(self):
        assert self.tool.name == "grep_search"

    def test_literal_match_line_numbers(self, workspace, no_rg):
        result = _run(self.tool.run({"query": "alpha"}))
        assert result.success is True
        assert "notes.md:1:" in result.output
        assert "beta.py:2:" in result.output
        assert "match(es)" in result.output

    def test_glob_filters_corpus(self, workspace, no_rg):
        result = _run(self.tool.run({"query": "alpha", "glob": "*.py"}))
        assert result.success is True
        assert "beta.py:" in result.output
        assert "notes.md" not in result.output

    def test_regex_mode_allowed(self, workspace, no_rg):
        result = _run(self.tool.run({"query": r"def\s+foo", "literal": False}))
        assert result.success is True
        assert "alpha.py:1:" in result.output

    def test_regex_policy_rejected_early(self, workspace, no_rg):
        result = _run(self.tool.run({"query": "(a+)+", "literal": False}))
        assert result.success is False
        assert "nested quantifier" in result.error

    def test_missing_query_fails(self, workspace, no_rg):
        result = _run(self.tool.run({}))
        assert result.success is False
        assert "query" in result.error

    def test_binary_and_oversized_files_skipped(self, tmp_path, no_rg, monkeypatch):
        monkeypatch.setattr(rst, "_active_cwd", lambda: tmp_path)
        (tmp_path / "binary.dat").write_bytes(b"alpha\x00beta" * 100)
        big = tmp_path / "huge.txt"
        with big.open("wb") as fh:
            fh.write(b"alpha" * (rst._MAX_FILE_BYTES // 4))
        (tmp_path / "plain.txt").write_text("alpha here\n", encoding="utf-8")

        result = _run(GrepSearchTool().run({"query": "alpha", "path": str(tmp_path)}))
        assert result.success is True
        assert "plain.txt:1:" in result.output
        assert "binary.dat" not in result.output
        assert "huge.txt" not in result.output

    def test_truncation_at_max_results(self, tmp_path, no_rg, monkeypatch):
        monkeypatch.setattr(rst, "_active_cwd", lambda: tmp_path)
        (tmp_path / "many.txt").write_text("hit\n" * 30, encoding="utf-8")
        result = _run(
            GrepSearchTool().run({"query": "hit", "path": str(tmp_path), "max_results": 5})
        )
        assert result.success is True
        assert "truncated at 5" in result.output

    def test_hard_deadline_kills_pathological_regex(
        self, tmp_path, no_rg, monkeypatch
    ):
        """A catastrophic pattern that PASSES the policy is still bounded by
        the parent's hard kill of the worker process (in-thread timers could
        not do this)."""
        monkeypatch.setattr(rst, "_active_cwd", lambda: tmp_path)
        monkeypatch.setattr(rst, "_WALL_CLOCK_S", 1.5)
        # No group-quantifier nesting (passes policy) but exponential on
        # FAILURE in Python's backtracking engine: the line never satisfies
        # the trailing 'c', so the engine explores every a/aa split.
        (tmp_path / "evil.txt").write_text("a" * 4000 + "x\n", encoding="utf-8")
        result = _run(
            GrepSearchTool().run(
                {"query": r"(?:a|aa)+c", "literal": False, "path": str(tmp_path)}
            )
        )
        # Either the deadline fired before any match (honest failure) or a
        # partial path returned — but it must NOT hang and must NOT claim a
        # clean completion after the deadline.
        if not result.success:
            assert "timed out" in result.error
        else:
            assert "partial" in result.output

    def test_rg_error_falls_back_instead_of_reporting_no_matches(
        self, workspace, monkeypatch
    ):
        """Regression: rg once got an unsupported flag (--timeout), exited 2, and
        the tool reported 'No matches' over a file that matched — an rg ERROR
        must never look like an empty result."""
        monkeypatch.setattr(rst.shutil, "which", lambda _name: "rg-pretend")
        monkeypatch.setattr(
            rst, "_run_rg", lambda *a, **k: ([], False, True)  # (lines, timed_out, failed)
        )
        result = _run(GrepSearchTool().run({"query": "alpha"}))
        assert result.success is True
        assert "notes.md:1:" in result.output, result.output
        assert "fell back to the python worker" in result.output

    @pytest.mark.skipif(not _HAS_RG, reason="ripgrep not on PATH")
    def test_real_rg_accepts_the_flags_we_pass(self, tmp_path):
        """Regression: run the REAL rg with our exact argv — a rejected flag
        makes it exit 2 and (without the fallback) hides every match."""
        (tmp_path / "hit.txt").write_text("needle here\n", encoding="utf-8")
        lines, timed_out, failed = rst._run_rg(
            tmp_path, shutil.which("rg"), "needle", True, 10, 10.0
        )
        assert failed is False, "rg rejected our flags"
        assert timed_out is False
        assert any("hit.txt:1:needle here" in ln.replace("\\", "/") for ln in lines), lines

    @pytest.mark.skipif(not _HAS_RG, reason="ripgrep not on PATH")
    def test_ripgrep_backend_parity(self, workspace, monkeypatch):
        rg_result = _run(GrepSearchTool().run({"query": "alpha"}))
        assert rg_result.success is True
        assert "backend: ripgrep" in rg_result.output
        assert "notes.md:1:" in rg_result.output

        monkeypatch.setattr(rst.shutil, "which", lambda _name: None)
        py_result = _run(GrepSearchTool().run({"query": "alpha"}))
        assert py_result.success is True
        assert "backend: python worker" in py_result.output
        rg_lines = {
            ln for ln in rg_result.output.splitlines() if ":" in ln and "backend" not in ln
        }
        py_lines = {
            ln for ln in py_result.output.splitlines() if ":" in ln and "backend" not in ln
        }
        assert rg_lines == py_lines, "backends must agree on matches"


# ---------------------------------------------------------------------------
# Registration & classification
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_tools_register_in_registry(self):
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools import register_all_tools

        register_all_tools()
        names = _AGENT_REGISTRY.available_names() or []
        assert "file_search" in names
        assert "grep_search" in names

    def test_toolset_exposure_lists(self):
        from navig.agent.toolsets import TOOLSETS

        for ts in ("code", "research"):
            listed = TOOLSETS[ts]
            assert "file_search" in listed
            assert "grep_search" in listed
        # Never on the lean default surface.
        assert "file_search" not in TOOLSETS["core"]
        assert "grep_search" not in TOOLSETS["core"]

    def test_parallel_classification(self):
        from navig.agent.toolsets import is_parallel_safe

        assert is_parallel_safe("file_search") is True
        assert is_parallel_safe("grep_search") is True

    def test_read_only_gate_verdicts(self):
        from navig.tools.approval import DESTRUCTIVE_TOOLS, is_destructive_tool

        assert "file_search" not in DESTRUCTIVE_TOOLS
        assert "grep_search" not in DESTRUCTIVE_TOOLS
        assert is_destructive_tool("file_search") is False
        assert is_destructive_tool("grep_search") is False

    def test_plan_interceptor_allows_search(self):
        from navig.agent.plan_mode import PlanInterceptor

        assert "file_search" in PlanInterceptor.READ_ONLY_TOOLS
        assert "grep_search" in PlanInterceptor.READ_ONLY_TOOLS

    def test_gate_returns_none_for_read_only_search(self):
        from navig.tools.approval import gate_agent_tool_call

        assert _run(gate_agent_tool_call("file_search")) is None
        assert _run(gate_agent_tool_call("grep_search")) is None
