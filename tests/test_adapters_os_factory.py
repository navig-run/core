"""Tests for navig.adapters.os.factory — detect_os, get_os_adapter, get_os_adapter_for_remote."""
from __future__ import annotations

import platform
from unittest.mock import patch

import pytest

from navig.adapters.os.factory import detect_os, get_os_adapter, get_os_adapter_for_remote
from navig.adapters.os.base import OSAdapter


class TestDetectOs:
    def test_windows(self) -> None:
        with patch.object(platform, "system", return_value="Windows"):
            assert detect_os() == "windows"

    def test_macos(self) -> None:
        with patch.object(platform, "system", return_value="Darwin"):
            assert detect_os() == "macos"

    def test_linux(self) -> None:
        with patch.object(platform, "system", return_value="Linux"):
            assert detect_os() == "linux"

    def test_unknown_falls_back_to_linux(self) -> None:
        with patch.object(platform, "system", return_value="FreeBSD"):
            assert detect_os() == "linux"


class TestGetOsAdapter:
    def test_windows_adapter(self) -> None:
        adapter = get_os_adapter("windows")
        assert isinstance(adapter, OSAdapter)

    def test_linux_adapter(self) -> None:
        adapter = get_os_adapter("linux")
        assert isinstance(adapter, OSAdapter)

    def test_macos_adapter(self) -> None:
        adapter = get_os_adapter("macos")
        assert isinstance(adapter, OSAdapter)

    def test_darwin_alias(self) -> None:
        adapter = get_os_adapter("darwin")
        assert isinstance(adapter, OSAdapter)

    def test_unsupported_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unsupported"):
            get_os_adapter("haiku")

    def test_case_insensitive(self) -> None:
        adapter = get_os_adapter("LINUX")
        assert isinstance(adapter, OSAdapter)

    def test_auto_detect_returns_adapter(self) -> None:
        adapter = get_os_adapter()
        assert isinstance(adapter, OSAdapter)


class TestGetOsAdapterForRemote:
    def test_windows_info_string(self) -> None:
        adapter = get_os_adapter_for_remote("Windows_NT")
        assert isinstance(adapter, OSAdapter)

    def test_mingw_info_string(self) -> None:
        adapter = get_os_adapter_for_remote("MINGW64_NT-10.0")
        assert isinstance(adapter, OSAdapter)

    def test_darwin_info_string(self) -> None:
        adapter = get_os_adapter_for_remote("Darwin")
        assert isinstance(adapter, OSAdapter)

    def test_linux_info_string(self) -> None:
        adapter = get_os_adapter_for_remote("Linux")
        assert isinstance(adapter, OSAdapter)

    def test_unknown_info_defaults_to_linux(self) -> None:
        adapter = get_os_adapter_for_remote("SomeOtherOS")
        assert isinstance(adapter, OSAdapter)
