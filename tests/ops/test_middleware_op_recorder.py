"""
Focused regressions for navig/cli/middleware.py — init_operation_recorder()
skip-record detection.

The core regression being guarded:

    command_str = " ".join(sys.argv[1:])  →  "--host prod db list"
    "-h" in command_str  →  True  (FALSE POSITIVE — "-h" is a substring of "--host")

After the fix, the skip check uses the non-global token string:
    _cmd_str_for_skip = "db list"
    "-h" in "db list"  →  False  (recording proceeds correctly)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import navig.cli.middleware as middleware_mod
import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _invoke_recorder(argv_tail: list[str], monkeypatch) -> dict:
    """
    Run init_operation_recorder() with the given argv tail and return ctx.obj.

    The operation_recorder dependency is mocked so the test is hermetic.
    Returns the final ctx.obj dict so callers can inspect whether
    ``_operation_record`` was set (recording happened) or not (skipped).
    """
    monkeypatch.setattr(middleware_mod.sys, "argv", ["navig", *argv_tail])

    fake_record = object()
    fake_recorder = MagicMock()
    fake_recorder.start_operation.return_value = fake_record

    ctx = MagicMock()
    ctx.obj = {}

    with patch("navig.operation_recorder.get_operation_recorder", return_value=fake_recorder):
        middleware_mod.init_operation_recorder(
            ctx=ctx, host=None, app=None, verbose=False
        )

    return ctx.obj


# ---------------------------------------------------------------------------
# Core false-positive guard (the Phase C fix)
# ---------------------------------------------------------------------------

def test_host_prefixed_db_command_is_not_skipped(monkeypatch):
    """``navig --host prod db list`` must NOT be skipped.

    Before the fix: ``"-h" in "--host prod db list"`` → True (false positive).
    After the fix:  ``"-h" in "db list"``              → False (correct).
    """
    obj = _invoke_recorder(["--host", "prod", "db", "list"], monkeypatch)
    assert "_operation_record" in obj, (
        "Recording was incorrectly skipped for navig --host prod db list "
        "(false-positive from '-h' being a substring of '--host')"
    )


def test_host_prefixed_run_command_is_not_skipped(monkeypatch):
    """``navig --host prod run ls`` must NOT be skipped."""
    obj = _invoke_recorder(["--host", "prod", "run", "ls"], monkeypatch)
    assert "_operation_record" in obj


def test_app_prefixed_file_command_is_not_skipped(monkeypatch):
    """``navig --app myapp file list /tmp`` must NOT be skipped."""
    obj = _invoke_recorder(["--app", "myapp", "file", "list", "/tmp"], monkeypatch)
    assert "_operation_record" in obj


# ---------------------------------------------------------------------------
# Legitimate skip cases — these must still be skipped after the fix
# ---------------------------------------------------------------------------

def test_bare_help_is_skipped(monkeypatch):
    """``navig help`` must be skipped (meta command)."""
    obj = _invoke_recorder(["help"], monkeypatch)
    assert "_operation_record" not in obj


# Note: bare `navig -h`, `navig --help`, and `navig --version` are NOT tested
# here for the skip-record behavior because:
#   1. `-h` is a value-consuming global flag (short form of --host).  When used
#      alone with no value, extract_non_global_tokens returns [] → empty
#      _cmd_str_for_skip → no skip.  This is benign: Typer intercepts those
#      invocations and exits before the atexit op-recorder fires.
#   2. `--help` and `--version` start with `--`, so extract_non_global_tokens
#      strips them → empty _cmd_str_for_skip → no skip.  Again benign for the
#      same reason.
# The entries "-h", "--help", "--version", "-v" in the skip list are now
# effectively dead code but are kept for documentation of intent.


def test_prefixed_help_is_skipped(monkeypatch):
    """``navig --host prod help`` must still be skipped (meta command after global flags)."""
    obj = _invoke_recorder(["--host", "prod", "help"], monkeypatch)
    assert "_operation_record" not in obj


def test_history_is_skipped(monkeypatch):
    """``navig history list`` must be skipped."""
    obj = _invoke_recorder(["history", "list"], monkeypatch)
    assert "_operation_record" not in obj
