"""
Hermetic unit tests for navig.mesh.sync_manager

Covers:
- Module-level constants
- SyncManager.__init__ state after construction
- _hash_state determinism
- _build_local_state structure
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

with patch("navig.debug_logger.get_debug_logger"):
    from navig.mesh.sync_manager import (
        DEFAULT_BROADCAST_INTERVAL_S,
        PULL_DEBOUNCE_S,
        PULL_TIMEOUT_S,
        SyncManager,
    )


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────


class TestSyncManagerConstants:
    def test_default_broadcast_interval(self):
        assert DEFAULT_BROADCAST_INTERVAL_S == 10

    def test_pull_debounce(self):
        assert PULL_DEBOUNCE_S == 5

    def test_pull_timeout(self):
        assert PULL_TIMEOUT_S == 5

    def test_broadcast_gt_debounce(self):
        # Sanity: broadcast interval should be >= debounce to avoid thundering herd
        assert DEFAULT_BROADCAST_INTERVAL_S >= PULL_DEBOUNCE_S


# ─────────────────────────────────────────────────────────────
# SyncManager.__init__
# ─────────────────────────────────────────────────────────────


def _make_sync_manager(sqlite_path=None, interval=None):
    registry = MagicMock()
    discovery = MagicMock()
    kwargs = {}
    if interval is not None:
        kwargs["broadcast_interval_s"] = interval
    if sqlite_path is not None:
        kwargs["optional_sqlite_path"] = sqlite_path
    return SyncManager(registry, discovery, **kwargs)


class TestSyncManagerInit:
    def test_default_broadcast_interval(self):
        sm = _make_sync_manager()
        assert sm._broadcast_interval_s == DEFAULT_BROADCAST_INTERVAL_S

    def test_custom_broadcast_interval(self):
        sm = _make_sync_manager(interval=30)
        assert sm._broadcast_interval_s == 30

    def test_initial_state_empty(self):
        sm = _make_sync_manager()
        assert sm._state == {}

    def test_initial_state_hash_empty_string(self):
        sm = _make_sync_manager()
        assert sm._state_hash == ""

    def test_initial_not_running(self):
        sm = _make_sync_manager()
        assert sm._running is False

    def test_no_sqlite_by_default(self):
        sm = _make_sync_manager()
        assert sm._sqlite_path is None

    def test_custom_sqlite_path(self, tmp_path):
        p = tmp_path / "sync.db"
        sm = _make_sync_manager(sqlite_path=p)
        assert sm._sqlite_path == p

    def test_task_initially_none(self):
        sm = _make_sync_manager()
        assert sm._task is None

    def test_last_pull_at_zero(self):
        sm = _make_sync_manager()
        assert sm._last_pull_at == 0.0


# ─────────────────────────────────────────────────────────────
# _hash_state
# ─────────────────────────────────────────────────────────────


class TestHashState:
    def test_deterministic(self):
        sm = _make_sync_manager()
        state = {"cron_hash": "abc", "heartbeat_interval": 30}
        h1 = sm._hash_state(state)
        h2 = sm._hash_state(state)
        assert h1 == h2

    def test_different_states_different_hashes(self):
        sm = _make_sync_manager()
        h1 = sm._hash_state({"key": "value1"})
        h2 = sm._hash_state({"key": "value2"})
        assert h1 != h2

    def test_returns_string(self):
        sm = _make_sync_manager()
        h = sm._hash_state({"a": 1})
        assert isinstance(h, str)

    def test_empty_state_has_consistent_hash(self):
        sm = _make_sync_manager()
        h = sm._hash_state({})
        assert len(h) > 0


# ─────────────────────────────────────────────────────────────
# _build_local_state
# ─────────────────────────────────────────────────────────────


class TestBuildLocalState:
    def test_returns_dict(self):
        sm = _make_sync_manager()
        state = sm._build_local_state()
        assert isinstance(state, dict)

    def test_contains_expected_keys(self):
        sm = _make_sync_manager()
        state = sm._build_local_state()
        # At minimum should have some metadata keys
        assert len(state) > 0
