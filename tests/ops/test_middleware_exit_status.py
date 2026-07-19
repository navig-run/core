"""
Regression: the CLI middleware must record a command's TRUE terminal status.

Bug (fixed here): the atexit operation-completion handler in
``navig/cli/middleware.py`` decided success from ``sys.exc_info()`` — which is
``(None, None, None)`` at interpreter shutdown AND in the handler's own worker
thread — so a genuinely-failed command like ``navig db tables --host missing``
(exit 2) was recorded as ``OperationStatus.SUCCESS``. That corrupted
``navig ledger show`` and made ``navig skill distill`` (T-069) file a failed
command as a working *step* instead of a *pitfall*: the ledger was lying.

Fix: ``navig.main.main()`` — the one point every invocation exits through —
reports the terminal exit code via ``middleware.note_exit_code()`` before the
process exits; the atexit handler reads that module global (shared across
threads, unlike ``sys.exc_info()``) and maps it to SUCCESS / FAILED / CANCELLED.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
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


@pytest.fixture(autouse=True)
def _reset_terminal_exit_code():
    """The captured exit code is a module global — keep it from bleeding across tests."""
    saved = mw._terminal_exit_code
    mw._terminal_exit_code = None
    try:
        yield
    finally:
        mw._terminal_exit_code = saved


# ---------------------------------------------------------------------------
# Pure mapping — exit code → OperationStatus (independently testable)
# ---------------------------------------------------------------------------


class TestExitCodeMapping:
    @pytest.mark.parametrize(
        "code,expected",
        [
            (None, 0),
            (0, 0),
            (1, 1),
            (2, 2),
            (130, 130),
            (True, 1),  # bool is an int subclass — normalise
            (False, 0),
            ("boom", 1),  # a message string → Python exits 1
            (object(), 1),
        ],
    )
    def test_exit_code_to_int(self, code, expected):
        assert mw._exit_code_to_int(code) == expected

    def test_zero_and_none_are_success(self):
        assert mw._status_for_exit_code(0) is OperationStatus.SUCCESS
        assert mw._status_for_exit_code(None) is OperationStatus.SUCCESS

    def test_nonzero_is_failed(self):
        assert mw._status_for_exit_code(2) is OperationStatus.FAILED
        assert mw._status_for_exit_code(1) is OperationStatus.FAILED
        assert mw._status_for_exit_code("boom") is OperationStatus.FAILED

    def test_sigint_is_cancelled(self):
        # 130 = SIGINT / Ctrl-C — a cancel, not a failed attempt; distill must
        # treat it as neither a step nor a pitfall.
        assert mw._status_for_exit_code(130) is OperationStatus.CANCELLED

    def test_note_exit_code_drives_exit_was_success(self):
        mw.note_exit_code(0)
        assert mw._exit_was_success() is True
        mw.note_exit_code(2)
        assert mw._exit_was_success() is False
        mw.note_exit_code(None)
        assert mw._exit_was_success() is True


# ---------------------------------------------------------------------------
# The atexit completion handler — the actual bug site
# ---------------------------------------------------------------------------


def _drive_completion_atexit(
    tmp_path: Path, monkeypatch, exit_code, *, note: bool = True
) -> OperationRecord:
    """Register + fire the middleware's completion atexit for one recorded op.

    When *note* is True the terminal exit code is announced via note_exit_code()
    (as ``navig.main`` does); returns the persisted record.
    """
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
    assert len(captured) == 1, "expected exactly one atexit completion handler"

    if note:
        mw.note_exit_code(exit_code)
    captured[0]()  # runs _on_exit → _write thread → join(1.0)

    stored = recorder.get_last_n(1)
    assert stored, "operation was not persisted to the ledger"
    return stored[0]


class TestCompletionAtexitRecordsTrueStatus:
    def test_nonzero_exit_records_failed(self, tmp_path, monkeypatch):
        # THE REGRESSION: before the fix this recorded SUCCESS, because the
        # handler read sys.exc_info() (empty at atexit / in the worker thread).
        rec = _drive_completion_atexit(tmp_path, monkeypatch, 2)
        assert rec.status is OperationStatus.FAILED
        assert rec.exit_code == 2

    def test_zero_exit_records_success(self, tmp_path, monkeypatch):
        rec = _drive_completion_atexit(tmp_path, monkeypatch, 0)
        assert rec.status is OperationStatus.SUCCESS
        assert rec.exit_code == 0

    def test_sigint_records_cancelled(self, tmp_path, monkeypatch):
        rec = _drive_completion_atexit(tmp_path, monkeypatch, 130)
        assert rec.status is OperationStatus.CANCELLED

    def test_default_uncaptured_is_success(self, tmp_path, monkeypatch):
        # Clean normal-return path: note_exit_code() never called → SUCCESS.
        rec = _drive_completion_atexit(tmp_path, monkeypatch, None, note=False)
        assert rec.status is OperationStatus.SUCCESS


# ---------------------------------------------------------------------------
# End-to-end honesty: a failed op distills as a PITFALL, not a step (T-069)
# ---------------------------------------------------------------------------


class TestFailedOpDistillsAsPitfall:
    def test_failed_command_is_a_pitfall_not_a_step(self, tmp_path):
        from navig.skill_distill import distill

        recorder = OperationRecorder(history_dir=tmp_path)
        ok = recorder.start_operation(
            command="navig host list", operation_type=OperationType.READ_QUERY
        )
        recorder.complete_operation(record=ok, success=True, exit_code=0)
        bad = recorder.start_operation(
            command="navig db tables --host missing",
            operation_type=OperationType.READ_QUERY,
        )
        # The status the fixed middleware now records for a non-zero exit.
        recorder.complete_operation(
            record=bad,
            success=False,
            exit_code=2,
            error="Host not found: missing",
            status=OperationStatus.FAILED,
        )

        records = sorted(recorder.get_last_n(10), key=lambda r: r.timestamp)
        result = distill(records)

        step_cmds = [s.command for s in result.steps]
        pitfall_cmds = [p.command for p in result.pitfalls]
        assert any("host list" in c for c in step_cmds)
        assert any("db tables" in c for c in pitfall_cmds), (
            "a genuinely-failed command must surface under Pitfalls"
        )
        assert not any("db tables" in c for c in step_cmds), (
            "a failed command must NOT be recorded as a working step"
        )


# ---------------------------------------------------------------------------
# Real subprocess — the full atexit path in a live process (the true proof)
# ---------------------------------------------------------------------------


class TestEndToEndLedgerHonesty:
    def _run_missing_host(self, tmp_path, argv_tail):
        """Run `navig --host missing <argv_tail…>` in an isolated config dir and
        return (proc, ledger_entries)."""
        core_dir = Path(__file__).resolve().parents[2]  # .../core (this worktree)
        # A cwd with its OWN .navig → find_app_root() resolves the ledger here,
        # deterministically, whatever .navig dirs live in ancestor paths.
        run_cwd = tmp_path / "space"
        cfg = run_cwd / ".navig"
        cfg.mkdir(parents=True)

        env = dict(os.environ)
        # Pin to THIS worktree's core (editable install points at the main checkout).
        env["PYTHONPATH"] = str(core_dir) + os.pathsep + env.get("PYTHONPATH", "")
        env["NAVIG_CONFIG_DIR"] = str(cfg)
        env["NAVIG_SKIP_ONBOARDING"] = "1"
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        proc = subprocess.run(
            [sys.executable, "-m", "navig", "--host", "missing", *argv_tail],
            cwd=str(run_cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        ledger = cfg / "history" / "operations.jsonl"
        entries = (
            [
                json.loads(line)
                for line in ledger.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if ledger.exists()
            else []
        )
        return proc, entries

    def test_failed_cli_command_records_failed(self, tmp_path):
        """`navig --host missing db tables` exits 2 (missing DATABASE arg) and
        must land in the ledger as FAILED, not SUCCESS."""
        proc, entries = self._run_missing_host(tmp_path, ["db", "tables"])
        assert proc.returncode == 2, (
            f"expected exit 2, got {proc.returncode}\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}"
        )

        db_ops = [e for e in entries if "db tables" in e.get("command", "")]
        assert db_ops, f"'db tables' was not recorded\nentries={entries}"
        last = db_ops[-1]
        assert last["status"] == "failed", (
            f"failed command recorded as {last['status']!r} — the ledger is lying"
        )
        assert last["exit_code"] == 2

    def test_silent_failure_path_records_failed(self, tmp_path):
        """The ACTUAL silent-failure path this task fixes: `db tables somedb`
        WITH a valid DATABASE arg reaches `_resolve_host_discovery`, which used
        to print "Host not found" and still exit 0 (recording SUCCESS). It must
        now exit 2 and record FAILED — distinct from the missing-arg usage error
        above, which exited 2 via Click regardless of this fix."""
        proc, entries = self._run_missing_host(tmp_path, ["db", "tables", "somedb"])
        assert proc.returncode == 2, (
            f"expected exit 2 (was 0 before the fix), got {proc.returncode}"
            f"\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}"
        )
        assert "Host not found: missing" in (proc.stdout + proc.stderr), (
            "the original error message must be preserved"
        )

        db_ops = [e for e in entries if "db tables" in e.get("command", "")]
        assert db_ops, f"'db tables somedb' was not recorded\nentries={entries}"
        last = db_ops[-1]
        assert last["status"] == "failed", (
            f"a genuinely-failed command recorded as {last['status']!r} — the ledger is lying"
        )
        assert last["exit_code"] == 2
