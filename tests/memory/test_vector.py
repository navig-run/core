"""
Tests for navig/memory/vector.py

Strategy:
- Test pure helpers (floats_to_blob, blob_to_floats) directly.
- Test VectorIndex with sqlite-vec unavailable (the default in CI).
  We reset the module-level _VEC_AVAILABLE cache via patching so tests
  are independent of import order.
"""

import sqlite3
import struct
from unittest.mock import patch

import pytest

import navig.memory.vector as _vec_mod
from navig.memory.vector import VectorIndex, blob_to_floats, floats_to_blob


# ---------------------------------------------------------------------------
# Helpers: floats_to_blob / blob_to_floats
# ---------------------------------------------------------------------------


class TestFloatsToBlob:
    def test_empty_list_returns_empty_bytes(self):
        assert floats_to_blob([]) == b""

    def test_single_float_round_trip(self):
        blob = floats_to_blob([1.0])
        assert blob == struct.pack("<1f", 1.0)
        assert blob_to_floats(blob) == pytest.approx([1.0])

    def test_two_floats_round_trip(self):
        original = [1.5, -0.5]
        blob = floats_to_blob(original)
        assert len(blob) == 8  # 2 * 4 bytes
        assert blob_to_floats(blob) == pytest.approx(original)

    def test_many_floats_round_trip(self):
        original = [float(i) * 0.1 for i in range(100)]
        assert blob_to_floats(floats_to_blob(original)) == pytest.approx(original, abs=1e-5)

    def test_blob_length_is_4_times_float_count(self):
        for n in (1, 4, 16, 64):
            floats = [0.0] * n
            assert len(floats_to_blob(floats)) == 4 * n

    def test_zeros(self):
        blob = floats_to_blob([0.0, 0.0])
        assert blob_to_floats(blob) == pytest.approx([0.0, 0.0])

    def test_negative_values(self):
        vals = [-1.0, -100.5, -0.001]
        assert blob_to_floats(floats_to_blob(vals)) == pytest.approx(vals, abs=1e-4)


# ---------------------------------------------------------------------------
# VectorIndex — unavailable sqlite-vec path
# ---------------------------------------------------------------------------


def _make_unavailable_index(conn, dimensions=4):
    """Create VectorIndex with sqlite-vec forced unavailable."""
    with patch.object(_vec_mod, "_VEC_AVAILABLE", False):
        with patch.object(_vec_mod, "_check_vec", return_value=False):
            idx = VectorIndex(conn, dimensions=dimensions)
    return idx


class TestVectorIndexUnavailable:
    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        yield c
        c.close()

    def test_available_is_false_when_no_sqlite_vec(self, conn):
        idx = _make_unavailable_index(conn)
        assert idx.available is False

    def test_dimensions_stored(self, conn):
        idx = _make_unavailable_index(conn, dimensions=512)
        assert idx.dimensions == 512

    def test_repr_contains_unavailable(self, conn):
        idx = _make_unavailable_index(conn)
        assert "unavailable" in repr(idx)

    def test_upsert_no_op_when_unavailable(self, conn):
        idx = _make_unavailable_index(conn)
        # Should not raise, no table created
        idx.upsert("chunk-1", [1.0, 0.0, 0.0, 0.0])

    def test_upsert_batch_returns_0_when_unavailable(self, conn):
        idx = _make_unavailable_index(conn)
        result = idx.upsert_batch([("c1", [1.0, 0.0, 0.0, 0.0])])
        assert result == 0

    def test_delete_no_op_when_unavailable(self, conn):
        idx = _make_unavailable_index(conn)
        idx.delete("nonexistent")  # must not raise

    def test_search_returns_empty_list_when_unavailable(self, conn):
        idx = _make_unavailable_index(conn)
        result = idx.search([1.0] * 4)
        assert result == []

    def test_count_returns_0_when_unavailable(self, conn):
        idx = _make_unavailable_index(conn)
        assert idx.count() == 0

    def test_migrate_text_embeddings_returns_0_when_unavailable(self, conn):
        idx = _make_unavailable_index(conn)
        assert idx.migrate_text_embeddings() == 0

    def test_upsert_batch_empty_returns_0(self, conn):
        idx = _make_unavailable_index(conn)
        assert idx.upsert_batch([]) == 0


# ---------------------------------------------------------------------------
# _check_vec module-level cache
# ---------------------------------------------------------------------------


class TestCheckVec:
    def test_returns_bool(self):
        # Resetting the cache so we get a live True/False value
        with patch.object(_vec_mod, "_VEC_AVAILABLE", None):
            result = _vec_mod._check_vec()
        assert isinstance(result, bool)

    def test_caches_result(self):
        """Calling _check_vec twice with cached value returns same result."""
        with patch.object(_vec_mod, "_VEC_AVAILABLE", False):
            r1 = _vec_mod._check_vec()
            r2 = _vec_mod._check_vec()
        assert r1 == r2 == False
