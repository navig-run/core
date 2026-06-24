"""
Batch 126 — tests for navig.agent.eyes and navig.commands.flux

Coverage targets:
  eyes.py:  SystemMetrics (dataclass, to_dict), Alert (dataclass, to_dict)
  flux.py:  module constants, _daemon_offline_msg, _lan_ip, _table
"""

from __future__ import annotations

import socket
from datetime import datetime
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from navig.agent.eyes import Alert, SystemMetrics
from navig.commands.flux import (
    _FLUX_READ_TIMEOUT,
    _FLUX_WRITE_TIMEOUT,
    _GW,
    _daemon_offline_msg,
    _lan_ip,
    _table,
)


# ===========================================================================
# SystemMetrics
# ===========================================================================


class TestSystemMetrics:
    def test_defaults(self):
        m = SystemMetrics()
        assert m.cpu_percent == 0.0
        assert m.memory_percent == 0.0
        assert m.memory_used_mb == 0.0
        assert m.disk_percent == 0.0
        assert m.disk_used_gb == 0.0
        assert m.load_average == (0.0, 0.0, 0.0)
        assert m.network_bytes_sent == 0
        assert m.network_bytes_recv == 0
        assert m.process_count == 0

    def test_timestamp_is_datetime(self):
        m = SystemMetrics()
        assert isinstance(m.timestamp, datetime)

    def test_set_values(self):
        m = SystemMetrics(cpu_percent=50.0, memory_percent=75.0, process_count=42)
        assert m.cpu_percent == 50.0
        assert m.memory_percent == 75.0
        assert m.process_count == 42

    def test_to_dict_keys(self):
        m = SystemMetrics()
        d = m.to_dict()
        for key in (
            "cpu_percent", "memory_percent", "memory_used_mb", "disk_percent",
            "disk_used_gb", "load_average", "network_bytes_sent", "network_bytes_recv",
            "process_count", "timestamp",
        ):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_cpu_value(self):
        m = SystemMetrics(cpu_percent=88.5)
        d = m.to_dict()
        assert d["cpu_percent"] == 88.5

    def test_to_dict_timestamp_is_string(self):
        m = SystemMetrics()
        d = m.to_dict()
        assert isinstance(d["timestamp"], str)
        datetime.fromisoformat(d["timestamp"])  # must be valid ISO

    def test_to_dict_load_average_tuple(self):
        m = SystemMetrics(load_average=(1.0, 2.0, 3.0))
        d = m.to_dict()
        assert d["load_average"] == (1.0, 2.0, 3.0)

    def test_to_dict_network_bytes(self):
        m = SystemMetrics(network_bytes_sent=1024, network_bytes_recv=2048)
        d = m.to_dict()
        assert d["network_bytes_sent"] == 1024
        assert d["network_bytes_recv"] == 2048


# ===========================================================================
# Alert
# ===========================================================================


class TestAlert:
    def test_required_fields(self):
        a = Alert(level="warning", category="cpu", message="CPU high")
        assert a.level == "warning"
        assert a.category == "cpu"
        assert a.message == "CPU high"

    def test_defaults(self):
        a = Alert(level="info", category="disk", message="Disk full")
        assert a.value is None
        assert a.threshold is None

    def test_timestamp_is_datetime(self):
        a = Alert(level="critical", category="memory", message="OOM")
        assert isinstance(a.timestamp, datetime)

    def test_to_dict_keys(self):
        a = Alert(level="warning", category="cpu", message="high CPU")
        d = a.to_dict()
        for key in ("level", "category", "message", "value", "threshold", "timestamp"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_level(self):
        a = Alert(level="critical", category="cpu", message="very high")
        d = a.to_dict()
        assert d["level"] == "critical"

    def test_to_dict_timestamp_iso(self):
        a = Alert(level="info", category="log", message="log rotated")
        d = a.to_dict()
        datetime.fromisoformat(d["timestamp"])

    def test_to_dict_value_set(self):
        a = Alert(level="warning", category="cpu", message="high", value=95.0, threshold=90.0)
        d = a.to_dict()
        assert d["value"] == 95.0
        assert d["threshold"] == 90.0

    def test_to_dict_value_none(self):
        a = Alert(level="info", category="cpu", message="ok")
        d = a.to_dict()
        assert d["value"] is None
        assert d["threshold"] is None


# ===========================================================================
# flux module constants
# ===========================================================================


class TestFluxConstants:
    def test_gw_is_localhost(self):
        assert "127.0.0.1" in _GW or "localhost" in _GW

    def test_gw_has_port(self):
        assert "8789" in _GW

    def test_flux_read_timeout_positive(self):
        assert _FLUX_READ_TIMEOUT > 0

    def test_flux_write_timeout_positive(self):
        assert _FLUX_WRITE_TIMEOUT > 0

    def test_write_timeout_gte_read_timeout(self):
        assert _FLUX_WRITE_TIMEOUT >= _FLUX_READ_TIMEOUT


# ===========================================================================
# _daemon_offline_msg
# ===========================================================================


class TestDaemonOfflineMsg:
    def test_returns_string(self):
        assert isinstance(_daemon_offline_msg(), str)

    def test_contains_offline(self):
        assert "OFFLINE" in _daemon_offline_msg().upper()

    def test_contains_start_hint(self):
        msg = _daemon_offline_msg()
        assert "start" in msg.lower()

    def test_contains_navig_service(self):
        msg = _daemon_offline_msg()
        assert "navig service" in msg.lower()

    def test_not_empty(self):
        assert len(_daemon_offline_msg().strip()) > 0


# ===========================================================================
# _lan_ip
# ===========================================================================


class TestLanIp:
    def test_returns_string(self):
        result = _lan_ip()
        assert isinstance(result, str)

    def test_returns_valid_ip_or_loopback(self):
        result = _lan_ip()
        parts = result.split(".")
        # Either a valid IPv4 or 127.0.0.1
        assert len(parts) == 4

    def test_fallback_on_socket_error(self):
        with patch("navig.commands.flux.socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock.connect.side_effect = OSError("unreachable")
            mock_sock_cls.return_value = mock_sock
            result = _lan_ip()
        assert result == "127.0.0.1"


# ===========================================================================
# _table
# ===========================================================================


class TestTable:
    def test_outputs_header(self, capsys):
        _table([["a", "b"]], ["H1", "H2"])
        captured = capsys.readouterr()
        assert "H1" in captured.out
        assert "H2" in captured.out

    def test_outputs_row_data(self, capsys):
        _table([["hello", "world"]], ["Col1", "Col2"])
        captured = capsys.readouterr()
        assert "hello" in captured.out
        assert "world" in captured.out

    def test_separator_present(self, capsys):
        _table([["x"]], ["Head"])
        captured = capsys.readouterr()
        assert "─" in captured.out

    def test_empty_rows(self, capsys):
        _table([], ["Col"])
        captured = capsys.readouterr()
        assert "Col" in captured.out

    def test_multiple_rows(self, capsys):
        rows = [["row1val1", "row1val2"], ["row2val1", "row2val2"]]
        _table(rows, ["A", "B"])
        captured = capsys.readouterr()
        assert "row1val1" in captured.out
        assert "row2val1" in captured.out

    def test_short_row_padded(self, capsys):
        # Row with fewer columns than headers should not raise
        _table([["only_one"]], ["H1", "H2"])
        captured = capsys.readouterr()
        assert "only_one" in captured.out
