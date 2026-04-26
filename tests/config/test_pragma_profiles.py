"""Hermetic unit tests for navig.storage.pragma_profiles."""

from __future__ import annotations

import pytest

from navig.storage.pragma_profiles import (
    BALANCED,
    DURABLE,
    FAST,
    PragmaProfile,
    profile_for_db,
)

# ---------------------------------------------------------------------------
# PragmaProfile.to_pragma_dict
# ---------------------------------------------------------------------------


class TestPragmaProfileToDict:
    def test_keys_present(self):
        d = FAST.to_pragma_dict()
        expected = {
            "journal_mode",
            "synchronous",
            "temp_store",
            "cache_size",
            "mmap_size",
            "foreign_keys",
            "wal_autocheckpoint",
            "busy_timeout",
            "page_size",
            "locking_mode",
            "auto_vacuum",
        }
        assert set(d.keys()) == expected

    def test_cache_size_negated(self):
        """cache_size should be negative KiB for SQLite."""
        d = FAST.to_pragma_dict()
        assert d["cache_size"] < 0
        assert d["cache_size"] == -FAST.cache_size_kb

    def test_foreign_keys_on(self):
        assert FAST.to_pragma_dict()["foreign_keys"] == "ON"

    def test_foreign_keys_off(self):
        p = PragmaProfile(name="test", foreign_keys=False)
        assert p.to_pragma_dict()["foreign_keys"] == "OFF"


# ---------------------------------------------------------------------------
# Constants — verify key differentiating properties
# ---------------------------------------------------------------------------


class TestFastProfile:
    def test_name(self):
        assert FAST.name == "FAST"

    def test_synchronous_off(self):
        assert FAST.synchronous == "OFF"

    def test_large_cache(self):
        assert FAST.cache_size_kb >= 16384


class TestBalancedProfile:
    def test_name(self):
        assert BALANCED.name == "BALANCED"

    def test_synchronous_normal(self):
        assert BALANCED.synchronous == "NORMAL"


class TestDurableProfile:
    def test_name(self):
        assert DURABLE.name == "DURABLE"

    def test_synchronous_full(self):
        assert DURABLE.synchronous == "FULL"

    def test_auto_vacuum_incremental(self):
        assert DURABLE.auto_vacuum == "INCREMENTAL"

    def test_no_mmap(self):
        assert DURABLE.mmap_size == 0


# ---------------------------------------------------------------------------
# profile_for_db
# ---------------------------------------------------------------------------


class TestProfileForDb:
    def test_runtime_db_fast(self):
        assert profile_for_db("runtime.db") is FAST

    def test_memory_db_balanced(self):
        assert profile_for_db("memory.db") is BALANCED

    def test_audit_db_durable(self):
        assert profile_for_db("audit.db") is DURABLE

    def test_vault_db_durable(self):
        assert profile_for_db("vault.db") is DURABLE

    def test_unknown_db_defaults_to_balanced(self):
        assert profile_for_db("unknown_totally_custom.db") is BALANCED

    def test_index_db_balanced(self):
        assert profile_for_db("index.db") is BALANCED
