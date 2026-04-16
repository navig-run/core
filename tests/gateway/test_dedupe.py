"""Tests for navig.gateway.dedupe — two-level idempotency cache."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from navig.gateway.dedupe import UpdateDedupe


@pytest.fixture()
def dedup(tmp_path: Path) -> UpdateDedupe:
    """Fresh dedupe instance backed by a temp directory."""
    return UpdateDedupe(
        memory_max=50,
        file_max=100,
        store_path=tmp_path / "dedup.json",
    )


class TestCheckAndRecord:
    def test_new_key_returns_false(self, dedup):
        assert dedup.check_and_record("update:1") is False

    def test_second_call_same_key_returns_true(self, dedup):
        dedup.check_and_record("update:1")
        assert dedup.check_and_record("update:1") is True

    def test_different_keys_both_new(self, dedup):
        assert dedup.check_and_record("a") is False
        assert dedup.check_and_record("b") is False

    def test_many_keys_no_false_positive(self, dedup):
        for i in range(40):
            assert dedup.check_and_record(f"k:{i}") is False
        for i in range(40):
            assert dedup.check_and_record(f"k:{i}") is True


class TestMemoryBound:
    def test_memory_evicts_oldest_entries(self, tmp_path):
        d = UpdateDedupe(memory_max=5, file_max=1_000, store_path=tmp_path / "d.json")
        for i in range(10):
            d.check_and_record(f"k{i}")
        # Memory should not exceed cap
        assert len(d._cache) <= 5

    def test_evicted_entry_not_found_in_memory(self, tmp_path):
        """After eviction from memory, a key is treated as new (no disk fallback on re-check)."""
        d = UpdateDedupe(memory_max=3, file_max=1_000, store_path=tmp_path / "d.json")
        d.check_and_record("first")
        d.check_and_record("second")
        d.check_and_record("third")
        # This evicts "first" from memory
        d.check_and_record("fourth")
        # "first" was written to disk during its initial record;
        # since _loaded=True now, it won't be re-read — it behaves as new in memory.
        # (This is intentional: memory cap trumps correctness for very old entries)
        assert "first" not in d._cache


class TestDiskPersistence:
    def test_disk_file_created_after_record(self, tmp_path):
        store = tmp_path / "dedup.json"
        d = UpdateDedupe(store_path=store)
        d.check_and_record("event-42")
        assert store.exists()

    def test_cold_start_recovery_from_disk(self, tmp_path):
        store = tmp_path / "dedup.json"

        # First instance records a key
        d1 = UpdateDedupe(memory_max=100, file_max=100, store_path=store)
        d1.check_and_record("seen-before")

        # Second instance (simulates process restart) loads from disk
        d2 = UpdateDedupe(memory_max=100, file_max=100, store_path=store)
        # _loaded is False; first call will trigger disk load
        assert d2.check_and_record("seen-before") is True

    def test_new_key_after_cold_start_returns_false(self, tmp_path):
        store = tmp_path / "dedup.json"
        d1 = UpdateDedupe(store_path=store)
        d1.check_and_record("old-key")

        d2 = UpdateDedupe(store_path=store)
        assert d2.check_and_record("brand-new-key") is False

    def test_disk_file_is_valid_json(self, tmp_path):
        store = tmp_path / "dedup.json"
        d = UpdateDedupe(store_path=store)
        for i in range(5):
            d.check_and_record(f"k{i}")
        data = json.loads(store.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert len(data) == 5

    def test_disk_pruned_to_file_max(self, tmp_path):
        store = tmp_path / "dedup.json"
        d = UpdateDedupe(memory_max=200, file_max=5, store_path=store)
        for i in range(20):
            d.check_and_record(f"k{i}")
        data = json.loads(store.read_text(encoding="utf-8"))
        assert len(data) <= 5

    def test_atomic_write_no_corruption_on_read(self, tmp_path):
        """Disk file should always be readable JSON after any number of writes."""
        store = tmp_path / "dedup.json"
        d = UpdateDedupe(memory_max=50, file_max=50, store_path=store)
        for i in range(50):
            d.check_and_record(f"ev-{i}")
        content = store.read_text(encoding="utf-8")
        parsed = json.loads(content)  # must not raise
        assert isinstance(parsed, dict)


class TestClearMethod:
    def test_clear_resets_memory_and_disk(self, tmp_path):
        store = tmp_path / "dedup.json"
        d = UpdateDedupe(store_path=store)
        d.check_and_record("x")
        assert store.exists()

        d.clear()
        assert len(d._cache) == 0
        assert not store.exists()

    def test_after_clear_same_key_is_new(self, dedup):
        dedup.check_and_record("recurring")
        dedup.clear()
        assert dedup.check_and_record("recurring") is False

    def test_missing_store_path_no_error_on_clear(self, tmp_path):
        d = UpdateDedupe(store_path=tmp_path / "nonexistent.json")
        d.clear()  # should not raise
