"""The authoritative ledger append must not queue behind a best-effort side-channel.

`OperationRecorder.complete_operation()` runs inside a one-second deadline:
`navig.cli.middleware`'s atexit completer starts a writer thread and joins it with
`timeout=1.0`. That deadline is harder than it looks — a thread started *inside* an
atexit handler is waited on by nobody, because threading's own shutdown join has
already run by then, so work still pending when the join expires is **abandoned**,
not merely delayed (pinned by the last test in this file).

`complete_operation` spent that budget in the wrong order. It performed its
best-effort dual-write to `audit.db` first — and constructing that store costs
50-407 ms on a cold config dir (disk + schema + lock contention) against ~3 ms for
the ledger append. On a loaded machine the side-channel overran the whole second and
the authoritative record was dropped: a command that really ran, and really failed,
left no line in `navig ledger show` at all. The in-flight marker is the backstop, but
it can only ever be reaped as `interrupted` — the command's true terminal status is
gone.

Fix: the authoritative append (and the in-flight clear that depends on it) happen
first; every best-effort write may only spend budget the ledger no longer needs.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from navig.operation_recorder import OperationRecord, OperationRecorder, OperationStatus

# Deliberately shorter than middleware's real 1.0 s so the test is quick. The
# defect is an ORDERING one, so any budget shorter than the stalled side-channel
# reproduces it; the numbers here only need to be unambiguous.
BUDGET_S = 0.5
STALL_S = 10.0


@pytest.fixture
def recorder(tmp_path: Path) -> OperationRecorder:
    return OperationRecorder(history_dir=tmp_path)


def _failed_op() -> OperationRecord:
    """A command that really ran and really failed — the case that must never vanish."""
    return OperationRecord(id="op-budget-regression", command="navig --host missing db tables")


def _complete(recorder: OperationRecorder, record: OperationRecord) -> None:
    recorder.complete_operation(
        record=record, success=False, exit_code=2, status=OperationStatus.FAILED
    )


def test_the_ledger_line_is_written_before_the_audit_dual_write(
    monkeypatch: pytest.MonkeyPatch, recorder: OperationRecorder
) -> None:
    """Ordering, asserted deterministically — no timing involved.

    The audit store is only ever reached after the ledger already holds the line.
    """
    seen_at_audit_time: list[str] = []

    class _Store:
        def log_event(self, **_kw: object) -> None:
            pass

    def _get_store() -> _Store:
        seen_at_audit_time.append(
            recorder.history_file.read_text(encoding="utf-8")
            if recorder.history_file.exists()
            else ""
        )
        return _Store()

    monkeypatch.setattr("navig.store.audit.get_audit_store", _get_store)

    record = _failed_op()
    _complete(recorder, record)

    assert seen_at_audit_time, "the audit dual-write never ran — this test proves nothing"
    assert record.id in seen_at_audit_time[0], (
        "the best-effort audit dual-write ran BEFORE the authoritative ledger append. "
        "It is charged to a one-second exit deadline it does not own: whatever it "
        "spends is taken from the only record of the command."
    )


def test_a_stalled_side_channel_cannot_swallow_the_authoritative_append(
    monkeypatch: pytest.MonkeyPatch, recorder: OperationRecorder
) -> None:
    """The real failure: a slow audit store must not cost the operator the record.

    Mirrors middleware's shape exactly — the completion runs on a thread joined with
    a deadline, and whatever has not landed by then is lost.
    """
    release = threading.Event()

    class _Store:
        def log_event(self, **_kw: object) -> None:
            pass

    def _slow_store() -> _Store:
        release.wait(timeout=STALL_S)  # a cold/contended audit.db
        return _Store()

    monkeypatch.setattr("navig.store.audit.get_audit_store", _slow_store)

    record = _failed_op()
    worker = threading.Thread(target=_complete, args=(recorder, record), daemon=True)
    worker.start()
    worker.join(timeout=BUDGET_S)

    try:
        written = (
            recorder.history_file.read_text(encoding="utf-8")
            if recorder.history_file.exists()
            else ""
        )
        assert record.id in written, (
            f"a failed command left NO ledger line within {BUDGET_S}s because a "
            "best-effort dual-write stalled ahead of it. In production that budget "
            "belongs to an atexit handler, so the append is not delayed — it is "
            "abandoned, and `navig ledger show` never learns the command ran."
        )
    finally:
        release.set()
        worker.join(timeout=STALL_S)


def test_an_atexit_thread_is_abandoned_when_its_join_expires(tmp_path: Path) -> None:
    """The platform fact the ordering above rests on — asserted, not assumed.

    `middleware` starts its writer with ``daemon=False``, which reads like a promise
    that the write completes. It is not one: atexit handlers run *after* threading's
    shutdown join, so a thread created there is nobody's responsibility. If this ever
    starts failing, CPython began waiting for such threads and the completion path
    could be simplified — check before assuming it is the test that is wrong.
    """
    marker = tmp_path / "wrote.txt"
    script = tmp_path / "atexit_probe.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import atexit, threading, time
            from pathlib import Path

            def _on_exit():
                def _write():
                    time.sleep(5.0)
                    Path(r"{marker}").write_text("WROTE", encoding="utf-8")
                t = threading.Thread(target=_write, daemon=False)
                t.start()
                t.join(timeout=0.05)

            atexit.register(_on_exit)
            """
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=60
    )

    assert proc.returncode == 0, f"probe failed: {proc.stderr}"
    assert not marker.exists(), (
        "a non-daemon thread started inside an atexit handler outlived the process — "
        "the completion path's deadline may no longer be a hard one"
    )
