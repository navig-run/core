"""
Focused regressions for navig/perf/profiler.py — _safe_argv().

Confirms that performance profile entries record the actual command tokens
(skipping global flag consumption) rather than raw sys.argv positions.

The core regression being guarded:

    navig --host prod host list

Before the fix: _safe_argv() returned "--host prod"
After the fix:  _safe_argv() returns  "host list"
"""
from __future__ import annotations

import sys

import pytest

import navig.perf.profiler as profiler_mod

pytestmark = pytest.mark.integration


def _safe_argv(argv_tail: list[str], monkeypatch) -> str:
    """Patch sys.argv and call _safe_argv()."""
    monkeypatch.setattr(profiler_mod.sys, "argv", ["navig", *argv_tail])
    return profiler_mod._safe_argv()


# ---------------------------------------------------------------------------
# Global-flag prefix stripping — the core Phase C regression
# ---------------------------------------------------------------------------

def test_safe_argv_strips_host_prefix(monkeypatch):
    """`navig --host prod host list` must record "host list", not "--host prod"."""
    result = _safe_argv(["--host", "prod", "host", "list"], monkeypatch)
    assert result == "host list"


def test_safe_argv_strips_app_prefix(monkeypatch):
    """`navig --app myapp db tables` must record "db tables"."""
    result = _safe_argv(["--app", "myapp", "db", "tables"], monkeypatch)
    assert result == "db tables"


def test_safe_argv_strips_short_host_prefix(monkeypatch):
    """`navig -h prod run ls` must record "run ls"."""
    result = _safe_argv(["-h", "prod", "run", "ls"], monkeypatch)
    assert result == "run ls"


def test_safe_argv_strips_short_app_prefix(monkeypatch):
    """`navig -p webapp file list /tmp` must record "file list"."""
    result = _safe_argv(["-p", "webapp", "file", "list", "/tmp"], monkeypatch)
    assert result == "file list"


# ---------------------------------------------------------------------------
# No global prefix — still works correctly
# ---------------------------------------------------------------------------

def test_safe_argv_no_prefix_two_tokens(monkeypatch):
    """`navig db tables` must record "db tables"."""
    result = _safe_argv(["db", "tables"], monkeypatch)
    assert result == "db tables"


def test_safe_argv_no_prefix_one_token(monkeypatch):
    """`navig version` must record "version"."""
    result = _safe_argv(["version"], monkeypatch)
    assert result == "version"


def test_safe_argv_no_prefix_long_command_truncates_to_two(monkeypatch):
    """`navig host monitor show --disk` must record only "host monitor" (first 2 tokens)."""
    result = _safe_argv(["host", "monitor", "show", "--disk"], monkeypatch)
    assert result == "host monitor"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_safe_argv_empty_argv(monkeypatch):
    """`navig` with no args records "(empty)"."""
    result = _safe_argv([], monkeypatch)
    assert result == "(empty)"


def test_safe_argv_global_only_no_command(monkeypatch):
    """`navig --host prod` with no command after records "(empty)"."""
    result = _safe_argv(["--host", "prod"], monkeypatch)
    assert result == "(empty)"
