"""A transient/unreadable read of the blocks lockfile must NOT wipe previously-pinned
block digests. `write_lock_entry` reads through json_io's `load_json_for_update`, which
RAISES `JsonReadError` on a file that exists-with-content but is transiently unreadable
(a Windows AV/backup lock) — so the install aborts the write instead of persisting a
lockfile that contains only the new block. The old `read_lockfile` returned an empty
lock on that lock (`except (OSError, ValueError)`), so the write silently dropped every
other block's pinned digest, disabling tamper detection for them.
"""

from __future__ import annotations

import pytest

import navig.core.json_io as jio
from navig.blocks.policy import lockfile_path, read_lockfile, write_lock_entry

pytestmark = pytest.mark.integration


def _pin(root, block_id):
    write_lock_entry(
        root, block_id, version="1.0.0", digest=f"sha256:{block_id}",
        source="store:x", trust="first-party", installed_at="2026-07-22T00:00:00Z",
    )


def test_write_lock_entry_is_additive_and_round_trips(tmp_path):
    _pin(tmp_path, "block-a")
    _pin(tmp_path, "block-b")
    blocks = read_lockfile(tmp_path)["blocks"]
    assert set(blocks) == {"block-a", "block-b"}
    assert blocks["block-a"]["digest"] == "sha256:block-a"


def test_transient_lock_aborts_write_without_wiping_pins(monkeypatch, tmp_path):
    _pin(tmp_path, "block-a")
    lf = lockfile_path(tmp_path)
    before = lf.read_text(encoding="utf-8")
    assert "block-a" in before

    def _locked(*_a, **_k):
        raise OSError("file is locked")  # a lock that survived json_io's retries

    monkeypatch.setattr(jio, "read_text_retrying", _locked)

    # The mutating path RAISES rather than reading an empty lock and wiping block-a.
    with pytest.raises(jio.JsonReadError):
        _pin(tmp_path, "block-b")

    monkeypatch.undo()
    assert lf.read_text(encoding="utf-8") == before  # block-a's pin intact, no block-b
    assert set(read_lockfile(tmp_path)["blocks"]) == {"block-a"}


def test_read_lockfile_degrades_on_unreadable(monkeypatch, tmp_path):
    _pin(tmp_path, "block-a")

    def _locked(*_a, **_k):
        raise OSError("file is locked")

    monkeypatch.setattr(jio, "read_text_retrying", _locked)
    # The read-only verify path must not crash — it degrades to an empty lock.
    lock = read_lockfile(tmp_path)
    assert lock == {"version": 1, "blocks": {}}
