"""
Task 1 — guard the ``sys.exc_info()``-at-atexit / worker-thread success bug CLASS.

PR #345 fixed two handlers in ``navig/cli/middleware.py`` that decided success
from ``sys.exc_info()``: it is ``(None, None, None)`` at interpreter shutdown
(the exception is already consumed) AND thread-local (a worker thread never sees
the main thread's exception), so both forced ``success=True`` and recorded every
failed command as a success.

This module locks that class shut and records the audit verdict for the nearby
handlers the PR-#345 agent flagged (fact-extraction, proactive, the connector
pool, the crash recorder):

- operation-complete atexit + debug-logger atexit — the two REAL bugs — now read
  the captured terminal exit code (``note_exit_code``); regression-covered here
  and in ``test_middleware_exit_status.py``.
- fact-extraction atexit — SAFE: it records that a command was RUN, makes no
  success/status claim, and does not read ``sys.exc_info()``.
- ``init_proactive_assistant`` — SAFE: registers no atexit; loads in a daemon
  thread, no success flag.
- ``mcp/tools/connectors.py`` pool-shutdown atexit — SAFE: a thread-pool
  ``shutdown``, no status decision.
- ``blackbox/crash.py`` ``sys.exc_info()`` — SAFE and out of scope: it is a
  crash RECORDER, called from a live ``except`` (or handed an explicit ``exc``),
  and it gracefully records an empty traceback; it never claims success.
"""

from __future__ import annotations

import ast
import types
from pathlib import Path

import pytest

import navig.cli.middleware as mw
from navig.operation_recorder import (
    OperationRecord,
    OperationRecorder,
    OperationStatus,
    OperationType,
)

pytestmark = pytest.mark.integration

_MIDDLEWARE_SRC = Path(mw.__file__)


@pytest.fixture(autouse=True)
def _reset_terminal_exit_code():
    saved = mw._terminal_exit_code
    mw._terminal_exit_code = None
    try:
        yield
    finally:
        mw._terminal_exit_code = saved


# ---------------------------------------------------------------------------
# The class guard: middleware.py must not CALL sys.exc_info()
# ---------------------------------------------------------------------------


def _sys_exc_info_calls(source: str) -> int:
    """Count real ``sys.exc_info()`` CALL sites (ignoring comments/docstrings)."""
    tree = ast.parse(source)
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (
            isinstance(fn, ast.Attribute)
            and fn.attr == "exc_info"
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "sys"
        ):
            n += 1
    return n


def test_middleware_never_calls_sys_exc_info():
    """The whole class prevention: no ``atexit``/worker-thread handler in the
    middleware may key success off ``sys.exc_info()`` — it reads the captured
    exit code instead. (Mentions in comments/docstrings are fine; a CALL is not.)
    """
    source = _MIDDLEWARE_SRC.read_text(encoding="utf-8")
    assert _sys_exc_info_calls(source) == 0, (
        "middleware.py calls sys.exc_info() — that is (None,None,None) at atexit "
        "and in a worker thread, and reintroduces the #345 lie. Read the exit code "
        "captured by note_exit_code() instead."
    )


def test_guard_detects_a_reintroduced_call():
    """The guard is not vacuous — it fires on a synthetic reintroduction."""
    bad = "import sys\ndef _on_exit():\n    ok = sys.exc_info()[0] is None\n"
    assert _sys_exc_info_calls(bad) == 1


# ---------------------------------------------------------------------------
# Regression: the operation-complete atexit honours the captured exit code
# ---------------------------------------------------------------------------


def _fire_operation_complete_atexit(tmp_path, monkeypatch, exit_code) -> OperationRecord:
    recorder = OperationRecorder(history_dir=tmp_path)
    record = recorder.start_operation(
        command="navig db tables --host missing",
        operation_type=OperationType.READ_QUERY,
    )
    ctx = types.SimpleNamespace(
        obj={
            "_operation_record": record,
            "_operation_recorder": recorder,
            "_operation_start": 0.0,
        }
    )
    captured: list = []
    monkeypatch.setattr(mw.atexit, "register", lambda fn: captured.append(fn))
    mw._register_operation_complete_atexit(ctx)
    assert len(captured) == 1
    mw.note_exit_code(exit_code)
    captured[0]()  # _on_exit → worker thread → join
    stored = recorder.get_last_n(1)
    assert stored, "operation was not persisted"
    return stored[0]


def test_operation_complete_records_failed_on_nonzero_exit(tmp_path, monkeypatch):
    rec = _fire_operation_complete_atexit(tmp_path, monkeypatch, 2)
    assert rec.status is OperationStatus.FAILED
    assert rec.exit_code == 2


def test_operation_complete_records_success_on_zero_exit(tmp_path, monkeypatch):
    rec = _fire_operation_complete_atexit(tmp_path, monkeypatch, 0)
    assert rec.status is OperationStatus.SUCCESS


# ---------------------------------------------------------------------------
# Regression: the debug-logger atexit honours the captured exit code
# ---------------------------------------------------------------------------


class _FakeDebugLogger:
    def __init__(self, *_a, **_k):
        self.log_path = "<fake>"
        self.ended_success: bool | None = None

    def log_command_start(self, *_a, **_k):
        pass

    def log_command_end(self, success: bool, message: str = "") -> None:
        self.ended_success = success


def _fire_debug_logger_atexit(monkeypatch, exit_code) -> _FakeDebugLogger:
    import navig.debug_logger as dl

    fake = _FakeDebugLogger()
    monkeypatch.setattr(dl, "DebugLogger", lambda *a, **k: fake)

    captured: list = []
    monkeypatch.setattr(mw.atexit, "register", lambda fn: captured.append(fn))

    ctx = types.SimpleNamespace(obj={})
    mw.init_debug_logger(
        ctx,
        debug_log=True,
        host=None,
        app=None,
        verbose=False,
        quiet=True,
        dry_run=False,
    )
    assert len(captured) == 1, "debug logger did not register its end handler"
    mw.note_exit_code(exit_code)
    captured[0]()
    return fake


def test_debug_logger_end_records_failure_on_nonzero_exit(monkeypatch):
    # THE REGRESSION (debug-logger half of #345): a failed command's debug log
    # claimed success because the atexit lambda read sys.exc_info().
    fake = _fire_debug_logger_atexit(monkeypatch, 2)
    assert fake.ended_success is False


def test_debug_logger_end_records_success_on_zero_exit(monkeypatch):
    fake = _fire_debug_logger_atexit(monkeypatch, 0)
    assert fake.ended_success is True


# ---------------------------------------------------------------------------
# Audit verdict: fact-extraction is exit-code-INDEPENDENT (records the attempt,
# claims no success) — the reason it is NOT a member of the bug class.
# ---------------------------------------------------------------------------


def test_fact_extraction_makes_no_success_determination():
    """``register_fact_extraction`` records that a command was run; it must not
    read the exit-code seam or ``sys.exc_info()`` to decide anything. This
    documents (and locks) the audited-safe verdict.
    """
    import inspect

    src = inspect.getsource(mw.register_fact_extraction)
    for forbidden in ("sys.exc_info", "_terminal_exit_code", "_exit_was_success"):
        assert forbidden not in src, (
            f"register_fact_extraction now references {forbidden!r} — if it has grown a "
            "success/status decision, it must use the captured exit code, not sys.exc_info()"
        )
