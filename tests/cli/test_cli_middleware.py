"""
Focused regressions for navig/cli/middleware.py — register_fact_extraction().

Covers:
  - plain help/version commands are skipped (no atexit registered)
  - global-flag-prefixed help/version commands are skipped
  - global-flag-prefixed meta commands (memory, kg) are skipped
  - global-only invocations (no command token) are skipped
  - normal commands register an atexit handler
"""
from __future__ import annotations

import atexit

import pytest

import navig.cli.middleware as middleware_mod

pytestmark = pytest.mark.integration


def _run(argv_tail: list[str], monkeypatch) -> list:
    """Run register_fact_extraction with given argv tail and collect atexit calls."""
    registered: list = []
    monkeypatch.setattr(middleware_mod.sys, "argv", ["navig", *argv_tail])
    monkeypatch.setattr(atexit, "register", lambda fn: registered.append(fn))
    middleware_mod.register_fact_extraction()
    return registered


# ---------------------------------------------------------------------------
# Commands that must always be skipped
# ---------------------------------------------------------------------------

def test_bare_help_is_skipped(monkeypatch):
    assert _run(["help"], monkeypatch) == []


def test_bare_version_is_skipped(monkeypatch):
    assert _run(["version"], monkeypatch) == []


def test_help_flag_is_skipped(monkeypatch):
    assert _run(["--help"], monkeypatch) == []


def test_version_flag_is_skipped(monkeypatch):
    assert _run(["--version"], monkeypatch) == []


def test_memory_is_skipped(monkeypatch):
    assert _run(["memory", "list"], monkeypatch) == []


def test_kg_is_skipped(monkeypatch):
    assert _run(["kg", "search", "foo"], monkeypatch) == []


def test_index_is_skipped(monkeypatch):
    assert _run(["index"], monkeypatch) == []


def test_history_is_skipped(monkeypatch):
    assert _run(["history"], monkeypatch) == []


def test_no_command_token_is_skipped(monkeypatch):
    """Global-only invocation with no real command token must be skipped."""
    assert _run(["--host", "prod"], monkeypatch) == []


# ---------------------------------------------------------------------------
# Global-flag-prefixed skip paths (the core fix verified by this test file)
# ---------------------------------------------------------------------------

def test_prefixed_global_help_is_skipped(monkeypatch):
    """`navig --host prod help db` must not register fact extraction."""
    assert _run(["--host", "prod", "help", "db"], monkeypatch) == []


def test_prefixed_global_version_is_skipped(monkeypatch):
    """`navig --host prod version` must not register fact extraction."""
    assert _run(["--host", "prod", "version"], monkeypatch) == []


def test_prefixed_global_help_flag_is_skipped(monkeypatch):
    """`navig --host prod --help` must not register fact extraction."""
    assert _run(["--host", "prod", "--help"], monkeypatch) == []


def test_prefixed_app_global_memory_is_skipped(monkeypatch):
    """`navig --app myapp memory list` must not register fact extraction."""
    assert _run(["--app", "myapp", "memory", "list"], monkeypatch) == []


def test_prefixed_global_kg_is_skipped(monkeypatch):
    """`navig --host prod kg search query` must not register fact extraction."""
    assert _run(["--host", "prod", "kg", "search", "query"], monkeypatch) == []


# ---------------------------------------------------------------------------
# Normal commands must register the atexit handler
# ---------------------------------------------------------------------------

def test_normal_command_registers_atexit(monkeypatch):
    """`navig run ls` must register the atexit fact extractor."""
    registered = _run(["run", "ls"], monkeypatch)
    assert len(registered) == 1


def test_prefixed_global_normal_command_registers_atexit(monkeypatch):
    """`navig --host prod host list` must still register fact extraction."""
    registered = _run(["--host", "prod", "host", "list"], monkeypatch)
    assert len(registered) == 1


def test_empty_argv_is_skipped(monkeypatch):
    """Invocation with no args must be silent (no atexit registered)."""
    assert _run([], monkeypatch) == []
