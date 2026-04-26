"""Hermetic unit tests for navig.local_operations."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.local_operations import LocalOperations, LocalSystemInfo

# ---------------------------------------------------------------------------
# LocalSystemInfo.to_dict — pure serialization
# ---------------------------------------------------------------------------


class TestLocalSystemInfoToDict:
    def _make(self, **kwargs) -> LocalSystemInfo:
        defaults = dict(
            hostname="myhost",
            os_name="linux",
            os_display_name="Ubuntu 22.04",
            is_admin=False,
            home_directory=Path("/home/user"),
            config_directory=Path("/home/user/.config"),
        )
        defaults.update(kwargs)
        return LocalSystemInfo(**defaults)

    def test_all_keys_present(self):
        d = self._make().to_dict()
        assert set(d.keys()) == {
            "hostname",
            "os_name",
            "os_display_name",
            "is_admin",
            "home_directory",
            "config_directory",
        }

    def test_paths_converted_to_strings(self):
        home = Path("/home/alice")
        conf = Path("/home/alice/.navig")
        d = self._make(home_directory=home, config_directory=conf).to_dict()
        assert isinstance(d["home_directory"], str)
        assert isinstance(d["config_directory"], str)
        assert d["home_directory"] == str(home)
        assert d["config_directory"] == str(conf)

    def test_bool_passthrough(self):
        assert self._make(is_admin=True).to_dict()["is_admin"] is True
        assert self._make(is_admin=False).to_dict()["is_admin"] is False

    def test_string_fields_passthrough(self):
        d = self._make(hostname="server01", os_name="windows", os_display_name="Windows 11").to_dict()
        assert d["hostname"] == "server01"
        assert d["os_name"] == "windows"
        assert d["os_display_name"] == "Windows 11"


# ---------------------------------------------------------------------------
# LocalOperations — lazy property behaviour
# ---------------------------------------------------------------------------


class TestLocalOperationsLazyConnection:
    def test_connection_created_on_first_access(self):
        ops = LocalOperations()
        with patch("navig.local_operations.LocalConnection") as MockConn:
            MockConn.return_value = MagicMock()
            ops._connection = None  # ensure unset
            # Trigger lazy load
            _ = ops.connection
            MockConn.assert_called_once()

    def test_connection_cached_on_second_access(self):
        ops = LocalOperations()
        with patch("navig.local_operations.LocalConnection") as MockConn:
            MockConn.return_value = MagicMock()
            ops._connection = None
            first = ops.connection
            second = ops.connection
            # Constructor called only once; same object returned
            assert MockConn.call_count == 1
            assert first is second

    def test_connection_not_created_if_already_set(self):
        ops = LocalOperations()
        mock_conn = MagicMock()
        ops._connection = mock_conn
        with patch("navig.local_operations.LocalConnection") as MockConn:
            result = ops.connection
            MockConn.assert_not_called()
            assert result is mock_conn


class TestLocalOperationsLazyOsAdapter:
    def test_os_adapter_created_on_first_access(self):
        ops = LocalOperations()
        with patch("navig.local_operations.get_os_adapter") as mock_fn:
            mock_fn.return_value = MagicMock()
            ops._os_adapter = None
            _ = ops.os_adapter
            mock_fn.assert_called_once()

    def test_os_adapter_cached_on_second_access(self):
        ops = LocalOperations()
        with patch("navig.local_operations.get_os_adapter") as mock_fn:
            mock_fn.return_value = MagicMock()
            ops._os_adapter = None
            first = ops.os_adapter
            second = ops.os_adapter
            assert mock_fn.call_count == 1
            assert first is second

    def test_os_adapter_not_created_if_already_set(self):
        ops = LocalOperations()
        mock_adapter = MagicMock()
        ops._os_adapter = mock_adapter
        with patch("navig.local_operations.get_os_adapter") as mock_fn:
            result = ops.os_adapter
            mock_fn.assert_not_called()
            assert result is mock_adapter


# ---------------------------------------------------------------------------
# LocalOperations — working_directory passed to connection
# ---------------------------------------------------------------------------


class TestLocalOperationsWorkingDirectory:
    def test_working_dir_forwarded_to_connection(self):
        wd = Path("/tmp/mywork")
        ops = LocalOperations(working_directory=wd)
        with patch("navig.local_operations.LocalConnection") as MockConn:
            MockConn.return_value = MagicMock()
            ops._connection = None
            _ = ops.connection
            MockConn.assert_called_once_with(working_directory=wd)

    def test_none_working_dir_accepted(self):
        ops = LocalOperations(working_directory=None)
        with patch("navig.local_operations.LocalConnection") as MockConn:
            MockConn.return_value = MagicMock()
            ops._connection = None
            _ = ops.connection
            MockConn.assert_called_once_with(working_directory=None)
