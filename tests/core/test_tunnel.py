"""Unit tests for navig.tunnel — helpers and TunnelManager core methods."""

from __future__ import annotations

import json
import socket
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.tunnel import (
    TunnelManager,
    _require_database_config,
    _require_server_identity,
)

# ─── _require_server_identity ────────────────────────────────────────────────


class TestRequireServerIdentity:
    def test_valid_config_returns_user_and_host(self):
        cfg = {"user": "deploy", "host": "10.0.0.1"}
        user, host = _require_server_identity(cfg)
        assert user == "deploy"
        assert host == "10.0.0.1"

    def test_strips_whitespace_from_values(self):
        cfg = {"user": "  admin  ", "host": "  example.com  "}
        user, host = _require_server_identity(cfg)
        assert user == "admin"
        assert host == "example.com"

    def test_missing_user_raises_value_error(self):
        with pytest.raises(ValueError, match="user"):
            _require_server_identity({"host": "10.0.0.1"})

    def test_missing_host_raises_value_error(self):
        with pytest.raises(ValueError, match="host"):
            _require_server_identity({"user": "admin"})

    def test_empty_user_raises_value_error(self):
        with pytest.raises(ValueError):
            _require_server_identity({"user": "", "host": "10.0.0.1"})

    def test_whitespace_only_user_raises_value_error(self):
        with pytest.raises(ValueError):
            _require_server_identity({"user": "   ", "host": "10.0.0.1"})

    def test_empty_host_raises_value_error(self):
        with pytest.raises(ValueError):
            _require_server_identity({"user": "admin", "host": ""})

    def test_empty_config_raises_value_error(self):
        with pytest.raises(ValueError):
            _require_server_identity({})

    def test_returns_tuple_of_strings(self):
        result = _require_server_identity({"user": "root", "host": "192.168.1.1"})
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(v, str) for v in result)


# ─── _require_database_config ────────────────────────────────────────────────


class TestRequireDatabaseConfig:
    def test_valid_dict_config_returned(self):
        db = {"host": "127.0.0.1", "port": 3306, "name": "myapp"}
        cfg = {"database": db}
        result = _require_database_config(cfg)
        assert result is db

    def test_missing_database_key_raises_value_error(self):
        with pytest.raises(ValueError, match="database"):
            _require_database_config({"user": "admin"})

    def test_non_dict_value_raises_value_error(self):
        with pytest.raises(ValueError, match="database"):
            _require_database_config({"database": "mydb"})

    def test_list_value_raises_value_error(self):
        with pytest.raises(ValueError):
            _require_database_config({"database": ["host", "port"]})

    def test_none_value_raises_value_error(self):
        with pytest.raises(ValueError):
            _require_database_config({"database": None})

    def test_empty_dict_is_accepted(self):
        result = _require_database_config({"database": {}})
        assert result == {}

    def test_integer_value_raises_value_error(self):
        with pytest.raises(ValueError):
            _require_database_config({"database": 3306})


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_cfg(tmp_path: Path):
    """Return a mock config_manager with tmp_path-backed files."""
    cfg = MagicMock()
    cfg.tunnels_file = tmp_path / "tunnels.json"
    cfg.log_file = tmp_path / "tunnel.log"
    return cfg


@contextmanager
def _noop_lock(self):
    """No-op replacement for TunnelManager._lock_tunnels_file."""
    yield


# ─── TunnelManager._find_available_port ──────────────────────────────────────


class TestFindAvailablePort:
    def test_returns_first_free_port(self, tmp_cfg):
        tm = TunnelManager(tmp_cfg)
        # Simulate first two ports occupied, third free
        bind_results = [OSError("occupied"), OSError("occupied"), None]
        call_count = 0

        real_socket = socket.socket

        class FakeSocket:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def bind(self, addr):
                nonlocal call_count
                result = bind_results[call_count]
                call_count += 1
                if result is not None:
                    raise result

        with patch("navig.tunnel.socket.socket", FakeSocket):
            port = tm._find_available_port(start_port=3307, end_port=3399)

        assert port == 3309  # 3307 occupied, 3308 occupied, 3309 free

    def test_returns_start_port_when_immediately_free(self, tmp_cfg):
        tm = TunnelManager(tmp_cfg)

        class FreeSocket:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def bind(self, addr):
                pass  # no exception = free

        with patch("navig.tunnel.socket.socket", FreeSocket):
            port = tm._find_available_port(start_port=4000, end_port=4099)

        assert port == 4000

    def test_raises_runtime_error_when_no_port_available(self, tmp_cfg):
        tm = TunnelManager(tmp_cfg)

        class FullSocket:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def bind(self, addr):
                raise OSError("occupied")

        with patch("navig.tunnel.socket.socket", FullSocket):
            with pytest.raises(RuntimeError, match="No available ports"):
                tm._find_available_port(start_port=4000, end_port=4005)


# ─── TunnelManager._load_tunnels ─────────────────────────────────────────────


class TestLoadTunnels:
    def test_returns_empty_dict_when_file_absent(self, tmp_cfg):
        tm = TunnelManager(tmp_cfg)
        with patch.object(tm, "_lock_tunnels_file", lambda: _noop_lock(tm)):
            result = tm._load_tunnels()
        assert result == {}

    def test_returns_dict_from_valid_json_file(self, tmp_cfg):
        data = {"prod": {"pid": 1234, "local_port": 3307, "started_at": "2025-01-01T00:00:00"}}
        tmp_cfg.tunnels_file.write_text(json.dumps(data), encoding="utf-8")
        tm = TunnelManager(tmp_cfg)
        with patch.object(tm, "_lock_tunnels_file", lambda: _noop_lock(tm)):
            result = tm._load_tunnels()
        assert result == data

    def test_returns_empty_dict_on_invalid_json(self, tmp_cfg):
        tmp_cfg.tunnels_file.write_text("{invalid json}", encoding="utf-8")
        tm = TunnelManager(tmp_cfg)
        with patch.object(tm, "_lock_tunnels_file", lambda: _noop_lock(tm)):
            result = tm._load_tunnels()
        assert result == {}

    def test_returns_empty_dict_on_empty_file(self, tmp_cfg):
        tmp_cfg.tunnels_file.write_text("", encoding="utf-8")
        tm = TunnelManager(tmp_cfg)
        with patch.object(tm, "_lock_tunnels_file", lambda: _noop_lock(tm)):
            result = tm._load_tunnels()
        assert result == {}


# ─── TunnelManager._save_tunnels ─────────────────────────────────────────────


class TestSaveTunnels:
    def test_creates_file_with_json_content(self, tmp_cfg):
        data = {"prod": {"pid": 999, "local_port": 3310}}
        tm = TunnelManager(tmp_cfg)
        with patch.object(tm, "_lock_tunnels_file", lambda: _noop_lock(tm)):
            tm._save_tunnels(data)
        saved = json.loads(tmp_cfg.tunnels_file.read_text(encoding="utf-8"))
        assert saved == data

    def test_creates_parent_directories(self, tmp_cfg):
        nested = tmp_cfg.tunnels_file.parent / "subdir" / "tunnels.json"
        tmp_cfg.tunnels_file = nested
        tm = TunnelManager(tmp_cfg)
        with patch.object(tm, "_lock_tunnels_file", lambda: _noop_lock(tm)):
            tm._save_tunnels({"key": "value"})
        assert nested.exists()

    def test_overwrites_existing_file(self, tmp_cfg):
        tmp_cfg.tunnels_file.write_text('{"old": true}', encoding="utf-8")
        data = {"new": "data"}
        tm = TunnelManager(tmp_cfg)
        with patch.object(tm, "_lock_tunnels_file", lambda: _noop_lock(tm)):
            tm._save_tunnels(data)
        saved = json.loads(tmp_cfg.tunnels_file.read_text(encoding="utf-8"))
        assert saved == {"new": "data"}
        assert "old" not in saved

    def test_saves_empty_dict(self, tmp_cfg):
        tm = TunnelManager(tmp_cfg)
        with patch.object(tm, "_lock_tunnels_file", lambda: _noop_lock(tm)):
            tm._save_tunnels({})
        saved = json.loads(tmp_cfg.tunnels_file.read_text(encoding="utf-8"))
        assert saved == {}

    def test_roundtrip_load_save(self, tmp_cfg):
        data = {"server-a": {"pid": 42, "local_port": 3307}}
        tm = TunnelManager(tmp_cfg)
        with patch.object(tm, "_lock_tunnels_file", lambda: _noop_lock(tm)):
            tm._save_tunnels(data)
            loaded = tm._load_tunnels()
        assert loaded == data
