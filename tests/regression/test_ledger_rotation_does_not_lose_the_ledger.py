"""A failed history rotation deleted the ledger and reported it as a warning.

`OperationRecorder._rotate()` trims the history file when it exceeds `max_entries`. It does
that by moving the ledger ASIDE first::

    self.history_file.rename(backup_file)      # operations.jsonl no longer exists
    ...                                        # write the trimmed replacement
    os.replace(tmp, self.history_file)         # put it back
    backup_file.unlink()

Anything that fails between the rename and the replace — a full disk, an antivirus or
indexer holding the directory, a permissions change — left **no operations.jsonl at all**.
The whole history sat in `operations.jsonl.bak`, which nothing reads; the next `record()`
recreated an empty file and started a FRESH hash chain; `navig undo` and `navig ledger
verify` saw an empty history. And the only signal was::

    ch.warning(f"Could not rotate history: {e}")

Rotation is housekeeping. Losing the audit trail to it is not an acceptable price, so the
handler now puts the original back. The file simply stays oversized until a later rotation
succeeds — which is strictly better than losing it.

The failure is injected at `os.replace`, the real boundary: that is the call that would fail
on a full disk or a locked directory, and it fires *after* the rename has already moved the
ledger away, which is precisely the window that made this destructive.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from navig.operation_recorder import OperationRecord, OperationRecorder, OperationType


def _recorder(tmp_path: Path, max_entries: int = 4) -> OperationRecorder:
    return OperationRecorder(history_dir=tmp_path, max_entries=max_entries)


def _record(rec: OperationRecorder, n: int) -> None:
    for i in range(n):
        rec.record(
            OperationRecord(
                operation_type=OperationType.LOCAL_COMMAND,
                command=f"echo {i}",
            )
        )


def test_a_failed_rotation_keeps_the_ledger(tmp_path, monkeypatch, capsys) -> None:
    """THE REGRESSION: the ledger must still exist, with its entries, after a failed rotate."""
    rec = _recorder(tmp_path)
    _record(rec, 4)
    before = rec.history_file.read_text(encoding="utf-8")
    assert before.strip(), "fixture wrote nothing"

    real_replace = os.replace

    def _boom(src, dst, *a, **kw):
        # Only sabotage the rotation's own swap; leave every other replace alone.
        if str(dst) == str(rec.history_file):
            raise OSError(28, "No space left on device")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", _boom)
    _record(rec, 3)  # pushes past max_entries and triggers _rotate()
    monkeypatch.setattr(os, "replace", real_replace)

    # `exists()` is NOT the discriminator: the rename leaves no operations.jsonl, but the
    # very record() that triggered the rotation then appends and RECREATES it. So on the
    # unfixed code the file is present and looks healthy — while containing only entries
    # written after the failure. The content check below is what actually catches it.
    assert rec.history_file.exists(), "no history ledger at all after a failed rotation"
    after = rec.history_file.read_text(encoding="utf-8")
    for line in before.splitlines():
        assert line in after, (
            "a pre-rotation entry was lost — the ledger was recreated from empty, so the "
            "hash chain restarted and every earlier operation is stranded in the .bak"
        )


def test_the_failure_is_reported_and_names_the_outcome(tmp_path, monkeypatch, capsys) -> None:
    """A restored ledger must still SAY the rotation failed — silence would hide unbounded growth."""
    rec = _recorder(tmp_path)
    _record(rec, 4)
    real_replace = os.replace

    def _boom(src, dst, *a, **kw):
        if str(dst) == str(rec.history_file):
            raise OSError(28, "No space left on device")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", _boom)
    _record(rec, 3)
    monkeypatch.setattr(os, "replace", real_replace)

    out = capsys.readouterr().out
    assert "rotate" in out.lower(), f"the failed rotation was silent:\n{out}"
    assert "restored" in out.lower(), (
        "the message does not tell the operator the ledger survived — the whole point of the "
        f"rollback is that they can tell this apart from data loss:\n{out}"
    )


def test_no_backup_is_left_behind_when_recovery_succeeds(tmp_path, monkeypatch) -> None:
    """The .bak must not linger: a stray one is indistinguishable from a half-done rotation."""
    rec = _recorder(tmp_path)
    _record(rec, 4)
    real_replace = os.replace

    def _boom(src, dst, *a, **kw):
        if str(dst) == str(rec.history_file):
            raise OSError(28, "No space left on device")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", _boom)
    _record(rec, 3)
    monkeypatch.setattr(os, "replace", real_replace)

    assert not rec.history_file.with_suffix(".jsonl.bak").exists(), (
        "the rollback renamed the backup back into place, so no .bak should remain"
    )


def test_rotation_still_works_when_nothing_fails(tmp_path) -> None:
    """Anti-vacuity, and the direction that matters most.

    Over-guarding here would silently disable trimming and let the ledger grow forever, which
    is the problem rotation exists to solve. A healthy rotate must still shrink the file and
    leave no backup.
    """
    rec = _recorder(tmp_path, max_entries=4)
    _record(rec, 12)

    lines = [ln for ln in rec.history_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) <= 4, f"rotation did not trim: {len(lines)} lines remain"
    assert not rec.history_file.with_suffix(".jsonl.bak").exists()
    # and what survived is still valid JSONL, not a truncated byte stream
    for ln in lines:
        json.loads(ln)


def test_backup_file_is_bound_before_the_try() -> None:
    """The handler references `backup_file`, so it must be bound on EVERY path into it.

    If the first `open()` in `_rotate` fails, the assignment inside the `try` has not run —
    and a handler naming it would raise NameError *inside an exception handler*, turning a
    reported problem into a crash. Asserted structurally rather than by fault injection:
    patching `builtins.open` globally also breaks the console the handler prints through, so
    that test passes or fails for reasons unrelated to the invariant.
    """
    import ast
    import inspect
    import textwrap

    fn = ast.parse(textwrap.dedent(inspect.getsource(OperationRecorder._rotate))).body[0]

    try_stmts = [n for n in fn.body if isinstance(n, ast.Try)]
    assert try_stmts, "_rotate no longer has a top-level try — re-derive this check"
    first_try = fn.body.index(try_stmts[0])

    bound_before = {
        t.id
        for stmt in fn.body[:first_try]
        if isinstance(stmt, ast.Assign)
        for t in stmt.targets
        if isinstance(t, ast.Name)
    }
    handler_names = {
        n.id
        for t in try_stmts
        for h in t.handlers
        for n in ast.walk(h)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }
    assert "backup_file" not in handler_names or "backup_file" in bound_before, (
        "_rotate's except handler reads `backup_file`, but it is only assigned inside the "
        "try — a failure before that assignment would raise NameError from the handler "
        "instead of restoring the ledger."
    )


@pytest.mark.parametrize("attr", ["_rotate"])
def test_rotate_is_still_the_method_under_test(attr: str) -> None:
    """Pin the name: a rename would make every test above pass while testing nothing."""
    assert callable(getattr(OperationRecorder, attr))
