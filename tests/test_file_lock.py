"""One writer at a time across processes — the lock behind the ledger append.

The regression it exists for: two ``navig`` commands finishing in the same second
each read the ledger's tail hash and each appended a child of it, so the file later
reported a tampering-shaped ``prev-hash mismatch`` (NEURON 2026-10-26, line 1971:
two ``vault get`` rows sharing ``prev=sha256:7cbd3``). These tests pin the lock
itself — deterministic exclusion across processes, a bounded wait, and re-entrancy
inside one thread — plus the ledger invariant under real concurrent appends.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from navig.file_lock import file_lock, lock_path_for

# A child that holds the lock long enough for the parent to collide with it.
_HOLDER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from navig.file_lock import file_lock

    target, hold, report = Path(sys.argv[1]), float(sys.argv[2]), Path(sys.argv[3])
    with file_lock(target, timeout=5.0):
        report.write_text("held", encoding="utf-8")
        time.sleep(hold)
    report.write_text("released", encoding="utf-8")
    """
)

_APPENDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from navig.operation_recorder import (
        OperationRecorder,
        OperationRecord,
        OperationStatus,
        OperationType,
    )

    history, count, tag = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    recorder = OperationRecorder(history_dir=history)
    for index in range(count):
        recorder.record(
            OperationRecord(
                command=f"navig test append {tag} {index}",
                operation_type=OperationType.LOCAL_COMMAND,
                status=OperationStatus.SUCCESS,
            )
        )
    """
)


def _run_child(script: str, *args: object, wait: float = 20.0) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", script, *[str(a) for a in args]],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for(path: Path, value: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text(encoding="utf-8") == value:
            return True
        time.sleep(0.02)
    return False


def test_a_peer_holding_the_lock_makes_us_wait_then_time_out(tmp_path) -> None:
    target = tmp_path / "operations.jsonl"
    report = tmp_path / "child.txt"
    child = _run_child(_HOLDER, target, 2.0, report)
    try:
        assert _wait_for(report, "held"), "the child never took the lock"

        started = time.monotonic()
        with pytest.raises(TimeoutError):
            with file_lock(target, timeout=0.3):
                pytest.fail("the lock was taken while a peer held it")
        waited = time.monotonic() - started

        assert 0.25 <= waited < 2.0, (
            f"the wait must be bounded by the timeout, waited {waited:.2f}s"
        )
    finally:
        child.wait(timeout=20)


def test_the_lock_is_free_again_after_the_peer_releases_it(tmp_path) -> None:
    target = tmp_path / "operations.jsonl"
    report = tmp_path / "child.txt"
    child = _run_child(_HOLDER, target, 0.2, report)
    child.wait(timeout=20)
    assert report.read_text(encoding="utf-8") == "released"

    with file_lock(target, timeout=2.0):
        inside = True
    assert inside


def test_the_lock_is_reentrant_within_one_thread(tmp_path) -> None:
    """The recorder holds the lock while it rotates; a nested call must not deadlock."""
    target = tmp_path / "operations.jsonl"
    with file_lock(target, timeout=0.5):
        with file_lock(target, timeout=0.5):
            passed_through = True
    assert passed_through


def test_the_lock_lives_on_a_sidecar_so_rotation_cannot_orphan_it(tmp_path) -> None:
    target = tmp_path / "operations.jsonl"
    target.write_text("", encoding="utf-8")
    with file_lock(target, timeout=1.0):
        assert lock_path_for(target).exists()
        # Rotation renames the ledger; the guard must survive that (same sidecar name).
        target.rename(tmp_path / "operations.jsonl.bak")
        with file_lock(target, timeout=1.0):
            nested_after_rotation = True
    assert nested_after_rotation


def test_concurrent_appends_keep_the_hash_chain_intact(tmp_path) -> None:
    """The real regression: parallel CLI processes must not fork the chain.

    Ten appenders start together and each writes four entries. Without the lock this
    reproduces the incident (`line 8: prev-hash mismatch`) within a few runs; with it
    the chain always verifies, because the tail read and the append are one critical
    section.
    """
    from navig.ledger_chain import verify_ledger

    history = tmp_path / "history"
    history.mkdir()
    children = [_run_child(_APPENDER, history, 4, f"p{n}") for n in range(10)]
    for child in children:
        out, err = child.communicate(timeout=60)
        assert child.returncode == 0, f"appender failed: {err or out}"

    result = verify_ledger(history / "operations.jsonl")

    assert result.total == 40, f"expected 40 appended entries, got {result.total}"
    assert result.breaks == [], [f"line {b.line}: {b.reason}" for b in result.breaks]
    assert result.verified == result.chained
