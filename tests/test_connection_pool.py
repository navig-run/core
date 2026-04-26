"""
Tests for navig/connection_pool.py

Covers SSHConnection metadata properties, SSHConnectionPool defaults, key
generation, singleton management, stats tracking, close_all, and LRU eviction.
All tests are hermetic — no real SSH connections; clients are replaced with mocks.
"""

from __future__ import annotations

import time
import threading
from unittest.mock import MagicMock, patch

import pytest

from navig.connection_pool import (
    PooledSSHConnection,
    SSHConnection,
    SSHConnectionPool,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_client() -> MagicMock:
    """Return a MagicMock that behaves like a paramiko.SSHClient."""
    c = MagicMock()
    transport = MagicMock()
    transport.is_active.return_value = True
    c.get_transport.return_value = transport
    return c


def _make_conn(host: str = "10.0.0.1", port: int = 22, user: str = "admin") -> SSHConnection:
    return SSHConnection(client=_mock_client(), host=host, port=port, user=user)


# ---------------------------------------------------------------------------
# SSHConnection metadata
# ---------------------------------------------------------------------------


class TestSSHConnectionKey:
    def test_key_format(self):
        c = _make_conn(host="myhost", port=22, user="alice")
        assert c.key == "alice@myhost:22"

    def test_key_custom_port(self):
        c = _make_conn(host="srv", port=2222, user="root")
        assert c.key == "root@srv:2222"

    def test_key_is_string(self):
        c = _make_conn()
        assert isinstance(c.key, str)


class TestSSHConnectionAgeAndIdle:
    def test_age_seconds_increases(self):
        c = _make_conn()
        before = c.age_seconds
        time.sleep(0.05)
        after = c.age_seconds
        assert after > before

    def test_idle_seconds_starts_near_zero(self):
        c = _make_conn()
        assert c.idle_seconds < 1.0

    def test_use_count_starts_zero(self):
        c = _make_conn()
        assert c.use_count == 0


class TestSSHConnectionIsAlive:
    def test_alive_when_transport_active(self):
        c = _make_conn()
        assert c.is_alive() is True

    def test_dead_when_no_transport(self):
        client = _mock_client()
        client.get_transport.return_value = None
        c = SSHConnection(client=client, host="h", port=22, user="u")
        assert c.is_alive() is False

    def test_dead_when_transport_inactive(self):
        client = _mock_client()
        client.get_transport.return_value.is_active.return_value = False
        c = SSHConnection(client=client, host="h", port=22, user="u")
        assert c.is_alive() is False

    def test_dead_when_get_transport_raises(self):
        client = _mock_client()
        client.get_transport.side_effect = RuntimeError("broken")
        c = SSHConnection(client=client, host="h", port=22, user="u")
        assert c.is_alive() is False


class TestSSHConnectionClose:
    def test_close_calls_client_close(self):
        client = _mock_client()
        c = SSHConnection(client=client, host="h", port=22, user="u")
        c.close()
        client.close.assert_called_once()

    def test_close_does_not_raise_on_exception(self):
        client = _mock_client()
        client.close.side_effect = RuntimeError("broken")
        c = SSHConnection(client=client, host="h", port=22, user="u")
        c.close()  # must not propagate


class TestPooledSSHConnectionAlias:
    def test_alias_is_same_class(self):
        assert PooledSSHConnection is SSHConnection


# ---------------------------------------------------------------------------
# SSHConnectionPool defaults
# ---------------------------------------------------------------------------


class TestSSHConnectionPoolDefaults:
    def test_max_connections(self):
        p = SSHConnectionPool()
        assert p.max_connections == SSHConnectionPool.DEFAULT_MAX_CONNECTIONS

    def test_max_age_seconds(self):
        p = SSHConnectionPool()
        assert p.max_age_seconds == SSHConnectionPool.DEFAULT_MAX_AGE_SECONDS

    def test_max_idle_seconds(self):
        p = SSHConnectionPool()
        assert p.max_idle_seconds == SSHConnectionPool.DEFAULT_MAX_IDLE_SECONDS

    def test_connect_timeout(self):
        p = SSHConnectionPool()
        assert p.connect_timeout == SSHConnectionPool.DEFAULT_CONNECT_TIMEOUT

    def test_custom_params_stored(self):
        p = SSHConnectionPool(max_connections=5, max_age_seconds=60)
        assert p.max_connections == 5
        assert p.max_age_seconds == 60

    def test_initial_active_count_zero(self):
        p = SSHConnectionPool()
        assert p.active_count == 0


# ---------------------------------------------------------------------------
# SSHConnectionPool._make_key()
# ---------------------------------------------------------------------------


class TestMakeKey:
    def test_standard_config(self):
        p = SSHConnectionPool()
        key = p._make_key({"host": "myhost", "port": 22, "user": "alice"})
        assert key == "alice@myhost:22"

    def test_default_port_22(self):
        p = SSHConnectionPool()
        key = p._make_key({"host": "srv", "user": "bob"})
        assert ":22" in key

    def test_custom_port(self):
        p = SSHConnectionPool()
        key = p._make_key({"host": "srv", "port": 2222, "user": "bob"})
        assert ":2222" in key


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------


class TestSingleton:
    def setup_method(self):
        SSHConnectionPool.reset_instance()

    def teardown_method(self):
        SSHConnectionPool.reset_instance()

    def test_get_instance_returns_pool(self):
        inst = SSHConnectionPool.get_instance()
        assert isinstance(inst, SSHConnectionPool)

    def test_get_instance_same_object(self):
        a = SSHConnectionPool.get_instance()
        b = SSHConnectionPool.get_instance()
        assert a is b

    def test_reset_instance_clears_singleton(self):
        a = SSHConnectionPool.get_instance()
        SSHConnectionPool.reset_instance()
        b = SSHConnectionPool.get_instance()
        assert a is not b


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_initial_stats_zero(self):
        p = SSHConnectionPool()
        s = p.stats
        assert s["hits"] == 0
        assert s["misses"] == 0
        assert s["connections_created"] == 0
        assert s["connections_closed"] == 0
        assert s["errors"] == 0

    def test_stats_includes_active_connections(self):
        p = SSHConnectionPool()
        assert "active_connections" in p.stats

    def test_stats_includes_hit_rate(self):
        p = SSHConnectionPool()
        assert "hit_rate" in p.stats

    def test_hit_rate_zero_when_no_activity(self):
        p = SSHConnectionPool()
        assert p.stats["hit_rate"] == 0.0


# ---------------------------------------------------------------------------
# close_all
# ---------------------------------------------------------------------------


class TestCloseAll:
    def test_close_all_empties_pool(self):
        p = SSHConnectionPool()
        # Inject mock connections directly
        conn1 = _make_conn(host="h1")
        conn2 = _make_conn(host="h2")
        p._connections[conn1.key] = conn1
        p._connections[conn2.key] = conn2
        assert p.active_count == 2
        p.close_all()
        assert p.active_count == 0

    def test_close_all_calls_close_on_each(self):
        p = SSHConnectionPool()
        conn = _make_conn()
        p._connections[conn.key] = conn
        p.close_all()
        conn.client.close.assert_called()

    def test_close_all_increments_closed_count(self):
        p = SSHConnectionPool()
        conn = _make_conn()
        p._connections[conn.key] = conn
        p.close_all()
        assert p.stats["connections_closed"] == 1


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


class TestRelease:
    def test_release_updates_last_used(self):
        p = SSHConnectionPool()
        conn = _make_conn()
        old_last_used = conn.last_used
        time.sleep(0.05)
        p.release(conn)
        assert conn.last_used > old_last_used


# ---------------------------------------------------------------------------
# _evict_oldest
# ---------------------------------------------------------------------------


class TestEvictOldest:
    def test_evicts_when_at_capacity(self):
        p = SSHConnectionPool(max_connections=2)
        conn1 = _make_conn(host="h1")
        conn2 = _make_conn(host="h2")
        p._connections[conn1.key] = conn1
        p._connections[conn2.key] = conn2
        assert p.active_count == 2
        p._evict_oldest()
        assert p.active_count == 1

    def test_no_evict_below_capacity(self):
        p = SSHConnectionPool(max_connections=5)
        conn1 = _make_conn(host="h1")
        p._connections[conn1.key] = conn1
        p._evict_oldest()
        assert p.active_count == 1


# ---------------------------------------------------------------------------
# get_connection_info
# ---------------------------------------------------------------------------


class TestGetConnectionInfo:
    def test_empty_pool_returns_empty_list(self):
        p = SSHConnectionPool()
        assert p.get_connection_info() == []

    def test_returns_dict_with_expected_keys(self):
        p = SSHConnectionPool()
        conn = _make_conn()
        p._connections[conn.key] = conn
        info = p.get_connection_info()
        assert len(info) == 1
        for key in ("key", "age_seconds", "idle_seconds", "use_count", "alive"):
            assert key in info[0]

    def test_alive_field_reflects_transport(self):
        p = SSHConnectionPool()
        conn = _make_conn()  # mock transport is_active=True
        p._connections[conn.key] = conn
        info = p.get_connection_info()
        assert info[0]["alive"] is True


# ---------------------------------------------------------------------------
# Thread safety smoke test
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_close_all_does_not_raise(self):
        p = SSHConnectionPool()
        for i in range(10):
            c = _make_conn(host=f"h{i}")
            p._connections[c.key] = c

        errors = []

        def close():
            try:
                p.close_all()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=close) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
