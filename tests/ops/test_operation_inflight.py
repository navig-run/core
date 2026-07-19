"""
The in-flight reaper (Task 2): an operation whose process is hard-killed before
it can complete must not vanish silently — it is aged out to a terminal
``interrupted`` status, honestly and chain-safely.

Design under test (navig.operation_inflight + OperationRecorder.reap_inflight):
the ledger writes a line only at completion, so a hard kill leaves an in-flight
MARKER but no ledger line. The reaper turns a marker whose owning process is
gone into ONE appended ``interrupted`` record (never a rewrite — the hash chain
stays intact), and never touches a marker whose process is still alive.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from navig import operation_inflight as inflight
from navig.ledger_chain import verify_ledger
from navig.operation_recorder import (
    OperationRecorder,
    OperationStatus,
    OperationType,
)

pytestmark = pytest.mark.integration

_DEAD_PID = 999_999  # not a live process on the test host


@pytest.fixture
def recorder(tmp_path) -> OperationRecorder:
    return OperationRecorder(history_dir=tmp_path)


def _mark(recorder: OperationRecorder, command: str, *, pid: int, host: str | None = None):
    """Start an op and hand-write its in-flight marker with a chosen PID —
    simulating a process that started the op and then died (dead pid) or is
    still running (live pid)."""
    rec = recorder.start_operation(command, OperationType.LOCAL_COMMAND, host=host)
    inflight.write_marker(
        recorder.history_dir,
        op_id=rec.id,
        command=rec.command,
        host=host,
        operation_type=rec.operation_type.value,
        working_dir="/tmp",
        pid=pid,
    )
    return rec


# ---------------------------------------------------------------------------
# The core premise: start writes NO ledger line (why the reaper must exist)
# ---------------------------------------------------------------------------


def test_started_operation_writes_no_ledger_line(recorder):
    """A started-but-never-completed op leaves NO ledger line — under a hard
    kill it would vanish entirely without the marker + reaper."""
    recorder.start_operation("navig db drop --host prod", OperationType.LOCAL_COMMAND)
    assert not recorder.history_file.exists()
    assert recorder.get_last_n(10) == []


# ---------------------------------------------------------------------------
# Reaping a stale (dead-PID) marker → one honest `interrupted` line
# ---------------------------------------------------------------------------


def test_dead_pid_marker_is_reaped_as_interrupted(recorder):
    rec = _mark(recorder, "navig db drop --host prod", pid=_DEAD_PID, host="prod")

    reaped = recorder.reap_inflight(max_age_seconds=0.0)

    assert len(reaped) == 1
    assert reaped[0]["id"] == rec.id
    ops = recorder.get_last_n(10)
    assert len(ops) == 1
    assert ops[0].status is OperationStatus.INTERRUPTED
    assert ops[0].command == "navig db drop --host prod"
    # The marker is gone after a successful reap.
    assert recorder.iter_inflight() == []


def test_reaped_record_carries_honest_metadata(recorder):
    _mark(recorder, "navig run deploy.sh --host web", pid=_DEAD_PID, host="web")
    recorder.reap_inflight(max_age_seconds=0.0)

    op = recorder.get_last_n(1)[0]
    assert op.exit_code == -1  # unknown — the process never reported one
    assert "reaped" in op.tags and "interrupted" in op.tags
    assert "without completing" in op.error


# ---------------------------------------------------------------------------
# Chain safety (T-067): reaping APPENDS, never rewrites → chain stays intact
# ---------------------------------------------------------------------------


def test_reaping_keeps_the_hash_chain_intact(recorder):
    # A couple of real completed ops first, so there is a chain to protect.
    ok = recorder.start_operation("navig host list", OperationType.READ_QUERY)
    recorder.complete_operation(ok, success=True)
    ok2 = recorder.start_operation("navig config get x", OperationType.READ_QUERY)
    recorder.complete_operation(ok2, success=True)

    _mark(recorder, "navig db drop --host prod", pid=_DEAD_PID)
    recorder.reap_inflight(max_age_seconds=0.0)

    result = verify_ledger(recorder.history_file)
    assert result.ok, f"chain broke: {[b.reason for b in result.breaks]}"
    assert result.status == "intact"
    assert result.chained == 3  # two completed + one reaped, all chained


# ---------------------------------------------------------------------------
# Safety: never reap a currently-running operation
# ---------------------------------------------------------------------------


def test_live_pid_marker_is_never_reaped(recorder):
    # This test process is unquestionably alive.
    _mark(recorder, "navig backup run --host prod", pid=os.getpid())

    reaped = recorder.reap_inflight(max_age_seconds=0.0)

    assert reaped == []
    assert len(recorder.iter_inflight()) == 1  # left in place — still running
    assert not recorder.history_file.exists()  # no premature interrupted line


def test_fresh_dead_marker_within_grace_window_is_not_reaped(recorder):
    """Even a dead PID is spared inside the age grace window — the secondary
    guard against a completion racing its marker delete + clock skew."""
    _mark(recorder, "navig db query", pid=_DEAD_PID)

    reaped = recorder.reap_inflight(max_age_seconds=3600.0)  # 1h window; marker is fresh

    assert reaped == []
    assert len(recorder.iter_inflight()) == 1


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_reaping_twice_does_not_double_write(recorder):
    _mark(recorder, "navig db drop --host prod", pid=_DEAD_PID)

    first = recorder.reap_inflight(max_age_seconds=0.0)
    second = recorder.reap_inflight(max_age_seconds=0.0)

    assert len(first) == 1
    assert second == []
    assert len(recorder.get_last_n(10)) == 1  # exactly one interrupted line


def test_completion_that_raced_the_delete_is_not_duplicated(recorder):
    """If the op already has a ledger line (its completion raced the marker
    delete), the reaper just drops the marker — no duplicate, no false status."""
    rec = recorder.start_operation("navig config set a b", OperationType.CONFIG_CHANGE)
    recorder.complete_operation(rec, success=True)  # writes the SUCCESS line + clears
    # Re-create a stale marker for the SAME id with a dead pid (the race).
    inflight.write_marker(
        recorder.history_dir,
        op_id=rec.id,
        command=rec.command,
        host=None,
        operation_type=rec.operation_type.value,
        working_dir="/tmp",
        pid=_DEAD_PID,
    )

    before = len(recorder.get_last_n(50))
    reaped = recorder.reap_inflight(max_age_seconds=0.0)

    assert reaped == []
    assert len(recorder.get_last_n(50)) == before  # no new line
    assert recorder.iter_inflight() == []  # stale marker cleared
    # The op's real status is preserved — not overwritten to interrupted.
    assert recorder.get_last_n(1)[0].status is OperationStatus.SUCCESS


# ---------------------------------------------------------------------------
# The normal (non-interrupted) path: completion clears the marker
# ---------------------------------------------------------------------------


def test_complete_operation_clears_the_marker(recorder):
    rec = _mark(recorder, "navig host list", pid=_DEAD_PID)
    assert len(recorder.iter_inflight()) == 1

    recorder.complete_operation(rec, success=True)

    assert recorder.iter_inflight() == []
    # And a subsequent reap finds nothing to reap.
    assert recorder.reap_inflight(max_age_seconds=0.0) == []


# ---------------------------------------------------------------------------
# dry-run reports without mutating
# ---------------------------------------------------------------------------


def test_dry_run_reports_but_does_not_record(recorder):
    _mark(recorder, "navig db drop --host prod", pid=_DEAD_PID)

    reaped = recorder.reap_inflight(max_age_seconds=0.0, dry_run=True)

    assert len(reaped) == 1
    assert not recorder.history_file.exists()  # nothing recorded
    assert len(recorder.iter_inflight()) == 1  # marker untouched
    # A real reap afterwards still works.
    assert len(recorder.reap_inflight(max_age_seconds=0.0)) == 1


# ---------------------------------------------------------------------------
# PID liveness helper
# ---------------------------------------------------------------------------


class TestPidLiveness:
    def test_own_pid_is_alive(self):
        assert inflight.pid_is_alive(os.getpid()) is True

    def test_dead_pid_is_not_alive(self):
        assert inflight.pid_is_alive(_DEAD_PID) is False

    def test_none_pid_is_not_alive(self):
        # No identity to protect → not alive (reap-eligible by age alone).
        assert inflight.pid_is_alive(None) is False

    def test_recycled_pid_is_not_alive(self):
        # Same PID, but a create_time that does not match the live process →
        # the PID was recycled, so the original process is gone.
        assert inflight.pid_is_alive(os.getpid(), create_time=1.0) is False

    def test_matching_create_time_is_alive(self):
        import psutil

        ct = psutil.Process(os.getpid()).create_time()
        assert inflight.pid_is_alive(os.getpid(), create_time=ct) is True


# ---------------------------------------------------------------------------
# Marker round-trip + malformed tolerance
# ---------------------------------------------------------------------------


def test_marker_round_trip(tmp_path):
    inflight.write_marker(
        tmp_path,
        op_id="op-test-1",
        command="navig db query",
        host="prod",
        operation_type="database_query",
        working_dir="/work",
        pid=1234,
    )
    markers = inflight.iter_markers(tmp_path)
    assert len(markers) == 1
    m = markers[0]
    assert m.op_id == "op-test-1"
    assert m.command == "navig db query"
    assert m.host == "prod"
    assert m.operation_type == "database_query"
    assert m.pid == 1234


def test_malformed_marker_is_skipped_not_fatal(tmp_path):
    d = inflight.inflight_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "garbage.json").write_text("{not json", encoding="utf-8")
    inflight.write_marker(
        tmp_path,
        op_id="op-ok",
        command="navig host list",
        host=None,
        operation_type="read_query",
        working_dir="/w",
        pid=_DEAD_PID,
    )
    markers = inflight.iter_markers(tmp_path)
    assert [m.op_id for m in markers] == ["op-ok"]  # garbage skipped, good one kept


def test_reaped_line_is_valid_json_with_interrupted_status(recorder):
    _mark(recorder, "navig db drop", pid=_DEAD_PID)
    recorder.reap_inflight(max_age_seconds=0.0)
    lines = [
        json.loads(line)
        for line in recorder.history_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
    assert lines[0]["status"] == "interrupted"
    assert "hash" in lines[0] and "prev" in lines[0]  # chained
