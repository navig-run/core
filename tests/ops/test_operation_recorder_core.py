import json
import shutil

import pytest

from navig.operation_recorder import OperationRecord, OperationRecorder

pytestmark = pytest.mark.integration


def test_record_indexes_new_entry_with_physical_line_offsets(tmp_path):
    recorder = OperationRecorder(history_dir=tmp_path)

    # Seed history with malformed and blank lines before a valid entry.
    recorder.history_file.write_text(
        "{bad-json\n\n"
        + json.dumps(
            OperationRecord(command="old", id="op-old", timestamp="2026-01-01T00:00:00Z").to_dict()
        )
        + "\n",
        encoding="utf-8",
    )

    new_record = OperationRecord(command="new")
    new_id = recorder.record(new_record)

    loaded = recorder.get_operation(new_id)
    assert loaded is not None
    assert loaded.id == new_id
    assert loaded.command == "new"


def test_record_falls_back_to_global_when_history_dir_deleted(tmp_path):
    """Regression: ``navig repo remove`` deletes the worktree whose ``.navig`` the
    recorder resolved to at startup, so the atexit completion writes into a dir
    that no longer exists. It must NOT lose the record (the old symptom was a
    "Could not record operation: No such file or directory" line) and must NOT
    recreate the removed worktree — it degrades to the global ledger instead.
    """
    from navig.config import get_config_manager

    # A worktree-local history dir: <wt>/.navig/history (what find_app_root picks
    # when navig runs with cwd inside a worktree that tracks its own .navig).
    wt = tmp_path / "wt"
    hist = wt / ".navig" / "history"
    recorder = OperationRecorder(history_dir=hist)

    first = recorder.record(OperationRecord(command="navig repo remove wt"))
    assert recorder.get_operation(first) is not None  # baseline append works
    assert recorder.history_dir == hist  # no redirect while dir is present

    # `navig repo remove` deletes the whole worktree, including its .navig.
    shutil.rmtree(wt)
    assert not hist.exists()

    # The next record must not raise, must land in the (isolated) global ledger,
    # and must NOT resurrect the removed worktree path.
    second = recorder.record(OperationRecord(command="post-removal completion"))

    global_hist = get_config_manager().global_config_dir / "history"
    assert recorder.history_dir == global_hist  # redirected to global
    assert not wt.exists()  # worktree not resurrected
    loaded = recorder.get_operation(second)
    assert loaded is not None
    assert loaded.command == "post-removal completion"
