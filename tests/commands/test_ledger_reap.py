"""
Surfaces for the in-flight reaper (Task 2): ``navig ledger reap`` records
interrupted operations honestly, and ``navig doctor`` (check_ledger) reports
them read-only without mutating the ledger.
"""

from __future__ import annotations

import json
import os
import types

import pytest

from navig import operation_inflight as inflight
from navig.operation_recorder import OperationRecorder, OperationStatus, OperationType

pytestmark = pytest.mark.integration

_DEAD_PID = 999_999


def _mark(recorder: OperationRecorder, command: str, *, pid: int) -> None:
    rec = recorder.start_operation(command, OperationType.LOCAL_COMMAND)
    inflight.write_marker(
        recorder.history_dir,
        op_id=rec.id,
        command=rec.command,
        host=None,
        operation_type=rec.operation_type.value,
        working_dir="/tmp",
        pid=pid,
    )


@pytest.fixture
def recorder(tmp_path, monkeypatch) -> OperationRecorder:
    rec = OperationRecorder(history_dir=tmp_path)
    # Both the ledger command and the doctor check resolve the recorder lazily
    # via get_operation_recorder(); point both at our isolated temp recorder.
    monkeypatch.setattr(
        "navig.operation_recorder.get_operation_recorder", lambda: rec
    )
    return rec


# ---------------------------------------------------------------------------
# navig ledger reap
# ---------------------------------------------------------------------------


def test_ledger_reap_records_interrupted(recorder, capsys):
    from navig.commands.ledger import ledger_reap

    _mark(recorder, "navig db drop --host prod", pid=_DEAD_PID)
    ctx = types.SimpleNamespace(obj={})

    ledger_reap(ctx, older_than=0.0, dry_run=False, json_out=True)

    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 1
    assert out["dry_run"] is False
    ops = recorder.get_last_n(10)
    assert len(ops) == 1
    assert ops[0].status is OperationStatus.INTERRUPTED


def test_ledger_reap_dry_run_records_nothing(recorder, capsys):
    from navig.commands.ledger import ledger_reap

    _mark(recorder, "navig db drop --host prod", pid=_DEAD_PID)
    ctx = types.SimpleNamespace(obj={})

    ledger_reap(ctx, older_than=0.0, dry_run=True, json_out=True)

    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 1
    assert out["dry_run"] is True
    assert not recorder.history_file.exists()  # nothing recorded
    assert len(recorder.iter_inflight()) == 1  # marker still present


def test_ledger_reap_leaves_running_op_alone(recorder, capsys):
    from navig.commands.ledger import ledger_reap

    _mark(recorder, "navig backup run", pid=os.getpid())  # this test process = alive
    ctx = types.SimpleNamespace(obj={})

    ledger_reap(ctx, older_than=0.0, dry_run=False, json_out=True)

    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 0
    assert not recorder.history_file.exists()


# ---------------------------------------------------------------------------
# navig doctor — check_ledger (read-only detection)
# ---------------------------------------------------------------------------


def test_check_ledger_empty_when_no_markers(recorder):
    from navig.commands.doctor import check_ledger

    assert check_ledger() == []


def test_check_ledger_reports_interrupted_and_running(recorder):
    from navig.commands.doctor import check_ledger

    _mark(recorder, "navig db drop --host prod", pid=_DEAD_PID)  # interrupted
    _mark(recorder, "navig backup run", pid=os.getpid())  # running

    rows = check_ledger()
    labels = {row[0]: row for row in [(r.label, r) for r in rows]}  # label -> row

    assert "Interrupted ops" in labels
    interrupted_row = labels["Interrupted ops"][1]
    assert interrupted_row[1] is False  # a real, non-green finding
    assert "never completed" in interrupted_row[2]

    assert "In-flight ops" in labels
    running_row = labels["In-flight ops"][1]
    assert running_row[1] is True  # running is a healthy/informational green

    # Detection is read-only: doctor must NOT have recorded anything.
    assert not recorder.history_file.exists()
    assert len(recorder.iter_inflight()) == 2
