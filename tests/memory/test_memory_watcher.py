from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from navig.memory.watcher import MemoryWatcher
import pytest

pytestmark = pytest.mark.integration


class _Result:
    def __init__(self, files_processed: int, chunks_created: int) -> None:
        self.files_processed = files_processed
        self.chunks_created = chunks_created


def _make_manager(memory_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        memory_dir=memory_dir,
        storage=SimpleNamespace(delete_file=MagicMock()),
        index_file=MagicMock(return_value=_Result(files_processed=1, chunks_created=2)),
    )


def test_process_pending_respects_debounce_window(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    watcher = MemoryWatcher(manager, debounce_seconds=10.0)

    watcher._pending_changes = {"note.md"}
    watcher._last_change_time = time.time()  # still inside debounce window

    watcher._process_pending()

    manager.index_file.assert_not_called()
    assert watcher._pending_changes == {"note.md"}


def test_process_pending_indexes_updated_file_and_calls_callback(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    callback = MagicMock()
    watcher = MemoryWatcher(manager, debounce_seconds=0.0, on_indexed=callback)

    # Updated file must exist on disk to be indexed
    updated = tmp_path / "note.md"
    updated.write_text("# Note\n\ncontent\n", encoding="utf-8")

    watcher._pending_changes = {"note.md"}
    watcher._last_change_time = 0.0

    watcher._process_pending()

    manager.index_file.assert_called_once_with(updated)
    callback.assert_called_once_with(1, 2)


def test_process_pending_handles_deletion_and_emits_callback(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    callback = MagicMock()
    watcher = MemoryWatcher(manager, debounce_seconds=0.0, on_indexed=callback)

    watcher._pending_changes = {"deleted:old.md"}
    watcher._last_change_time = 0.0

    watcher._process_pending()

    manager.storage.delete_file.assert_called_once_with("old.md")
    manager.index_file.assert_not_called()
    callback.assert_called_once_with(0, 0)


def test_process_pending_skipped_update_does_not_emit_callback(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    manager.index_file = MagicMock(return_value=_Result(files_processed=0, chunks_created=0))

    callback = MagicMock()
    watcher = MemoryWatcher(manager, debounce_seconds=0.0, on_indexed=callback)

    updated = tmp_path / "noop.md"
    updated.write_text("# No-op\n\ncontent\n", encoding="utf-8")

    watcher._pending_changes = {"noop.md"}
    watcher._last_change_time = 0.0

    watcher._process_pending()

    manager.index_file.assert_called_once_with(updated)
    callback.assert_not_called()


def test_process_pending_normalizes_deleted_path_separators(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    watcher = MemoryWatcher(manager, debounce_seconds=0.0)

    watcher._pending_changes = {"deleted:folder\\old.md"}
    watcher._last_change_time = 0.0

    watcher._process_pending()

    manager.storage.delete_file.assert_called_once_with("folder/old.md")


def test_process_pending_normalizes_updated_path_separators(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    watcher = MemoryWatcher(manager, debounce_seconds=0.0)

    nested = tmp_path / "folder"
    nested.mkdir(parents=True, exist_ok=True)
    updated = nested / "note.md"
    updated.write_text("# Note\n\nnormalized path\n", encoding="utf-8")

    watcher._pending_changes = {"folder\\note.md"}
    watcher._last_change_time = 0.0

    watcher._process_pending()

    manager.index_file.assert_called_once_with(updated)


def test_scan_files_returns_posix_relative_paths(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    watcher = MemoryWatcher(manager, debounce_seconds=0.0)

    nested = tmp_path / "sub" / "dir"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "doc.md").write_text("# Doc\n", encoding="utf-8")

    mtimes = watcher._scan_files()
    assert "sub/dir/doc.md" in mtimes
