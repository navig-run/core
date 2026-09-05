"""Regression guard: a failed READ of a RuntimeStore file must never become a
destructive WRITE that wipes the node/mission/receipt audit trail.

Before the fix, ``RuntimeStore._load`` wrapped all three file reads in one outer
``try/except`` that swallowed any failure and left the in-memory dicts empty; the
next ``flush()`` then wrote that emptiness back over every collection. The
highest-frequency trigger is ``navig.blocks.receipts.persist_receipt``, which
constructs a fresh ``RuntimeStore()`` (load → record → flush) on *every* block
apply — so a single transient lock on ``receipts.json`` at that moment destroyed
the entire receipt history.

These tests pin the invariant from the CLAUDE.md sharp edge: an unreadable file is
NOT an empty file, and flush() refuses to overwrite a collection it could not read.
"""

from __future__ import annotations

import json
from pathlib import Path

from navig.contracts.execution_receipt import ExecutionReceipt, ReceiptOutcome
from navig.contracts.store import RuntimeStore


def _receipt(rid: str) -> ExecutionReceipt:
    return ExecutionReceipt(
        mission_id=f"m-{rid}",
        node_id="node-1",
        title=rid,
        capability="cap",
        outcome=ReceiptOutcome.SUCCEEDED,
        completed_at="2026-01-01T00:00:00Z",
        receipt_id=rid,
    )


def _write_receipts(d: Path, receipts) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "receipts.json").write_text(
        json.dumps([r.to_dict() for r in receipts], indent=2), encoding="utf-8"
    )


def _receipt_ids_on_disk(d: Path) -> set[str]:
    # read_bytes so a patched Path.read_text can't interfere with the assertion
    return {r["receipt_id"] for r in json.loads((d / "receipts.json").read_bytes())}


def test_healthy_roundtrip_persists_and_reloads(tmp_path):
    d = tmp_path / "runtime"
    s1 = RuntimeStore(store_dir=d)
    s1.record_receipt(_receipt("r1"))
    s1.record_receipt(_receipt("r2"))
    s1.flush()

    s2 = RuntimeStore(store_dir=d)
    assert {r.receipt_id for r in s2.list_receipts()} == {"r1", "r2"}
    assert s2._load_failed == set()


def test_corrupt_receipts_file_is_not_wiped_by_flush(tmp_path):
    """TEETH: a corrupt receipts.json must not be overwritten with the empty (or
    single-new-receipt) in-memory state. Fails on the old code, which wrote [r-new]."""
    d = tmp_path / "runtime"
    d.mkdir(parents=True)
    corrupt = "{ this is not valid json "
    (d / "receipts.json").write_text(corrupt, encoding="utf-8")

    store = RuntimeStore(store_dir=d)
    assert "receipts.json" in store._load_failed  # exists but unparseable

    store.record_receipt(_receipt("r-new"))
    store.flush()  # must refuse to overwrite the unreadable file

    assert (d / "receipts.json").read_text(encoding="utf-8") == corrupt


def test_unreadable_receipts_file_preserves_valid_history(tmp_path, monkeypatch):
    """TEETH: a transient lock on receipts.json during load must not let the next
    flush wipe the real history. Old code swallowed the read error and wrote [r-new]."""
    d = tmp_path / "runtime"
    _write_receipts(d, [_receipt("keep-1"), _receipt("keep-2")])

    orig_read_text = Path.read_text

    def flaky_read_text(self, *args, **kwargs):
        if self.name == "receipts.json":
            raise PermissionError("locked by another process")
        return orig_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    store = RuntimeStore(store_dir=d)
    assert "receipts.json" in store._load_failed

    store.record_receipt(_receipt("r-new"))
    store.flush()  # still locked → skip the write, preserve the history on disk

    ids = _receipt_ids_on_disk(d)
    assert {"keep-1", "keep-2"} <= ids  # history intact, not replaced by {r-new}


def test_transient_read_failure_self_heals_on_next_flush(tmp_path, monkeypatch):
    """A lock that clears between load and flush: flush re-reads, folds the on-disk
    history back in (keeping the in-session receipt), and persists both.

    Patches the store's ``read_text_retrying`` symbol so exactly one read fails —
    ``read_text_retrying`` itself already retries within a single load, so a
    Path-level 'fail once' would be absorbed there and never reach ``_load_failed``.
    """
    import navig.contracts.store as store_mod

    d = tmp_path / "runtime"
    _write_receipts(d, [_receipt("hist-1")])

    calls = {"n": 0}
    orig = store_mod.read_text_retrying

    def once_flaky(path, **kwargs):
        if Path(path).name == "receipts.json":
            calls["n"] += 1
            if calls["n"] == 1:  # fail the load read; let the flush read succeed
                raise PermissionError("briefly locked")
        return orig(path, **kwargs)

    monkeypatch.setattr(store_mod, "read_text_retrying", once_flaky)

    store = RuntimeStore(store_dir=d)
    assert "receipts.json" in store._load_failed

    store.record_receipt(_receipt("new-1"))
    store.flush()  # self-heal: re-read succeeds now → merge history + persist

    assert "receipts.json" not in store._load_failed
    assert _receipt_ids_on_disk(d) == {"hist-1", "new-1"}


def test_corrupt_nodes_file_does_not_block_receipt_persistence(tmp_path):
    """TEETH: per-file isolation. A corrupt nodes.json must not (a) block receipts
    from persisting nor (b) get itself overwritten. Old code's single outer try
    aborted all three loads and the flush wrote [] over the corrupt nodes file."""
    d = tmp_path / "runtime"
    d.mkdir(parents=True)
    corrupt_nodes = "{ broken "
    (d / "nodes.json").write_text(corrupt_nodes, encoding="utf-8")

    store = RuntimeStore(store_dir=d)
    assert "nodes.json" in store._load_failed
    assert "receipts.json" not in store._load_failed  # unaffected by the bad file

    store.record_receipt(_receipt("r-iso"))
    store.flush()

    assert _receipt_ids_on_disk(d) == {"r-iso"}  # receipts persisted despite bad nodes
    assert (d / "nodes.json").read_text(encoding="utf-8") == corrupt_nodes  # preserved
