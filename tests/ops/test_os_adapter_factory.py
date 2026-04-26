"""Hermetic unit tests for navig.adapters.os.factory."""
from __future__ import annotations

from unittest.mock import MagicMock, mock_open, patch

import pytest

# ---------------------------------------------------------------------------
# detect_os
# ---------------------------------------------------------------------------


class TestDetectOs:
    def test_windows(self):
        from navig.adapters.os.factory import detect_os

        with patch("platform.system", return_value="Windows"):
            assert detect_os() == "windows"

    def test_macos(self):
        from navig.adapters.os.factory import detect_os

        with patch("platform.system", return_value="Darwin"):
            assert detect_os() == "macos"

    def test_linux(self):
        from navig.adapters.os.factory import detect_os

        with patch("platform.system", return_value="Linux"):
            assert detect_os() == "linux"

    def test_unknown_falls_back_to_linux(self):
        from navig.adapters.os.factory import detect_os

        with patch("platform.system", return_value="FreeBSD"):
            assert detect_os() == "linux"

    def test_case_insensitive_input(self):
        from navig.adapters.os.factory import detect_os

        with patch("platform.system", return_value="WINDOWS"):
            assert detect_os() == "windows"


# ---------------------------------------------------------------------------
# detect_linux_distro
# ---------------------------------------------------------------------------


class TestDetectLinuxDistro:
    def test_reads_id_from_os_release(self):
        from navig.adapters.os.factory import detect_linux_distro

        content = 'NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="22.04"\n'
        with patch("builtins.open", mock_open(read_data=content)):
            assert detect_linux_distro() == "ubuntu"

    def test_strips_quotes_from_id(self):
        from navig.adapters.os.factory import detect_linux_distro

        content = 'ID="debian"\n'
        with patch("builtins.open", mock_open(read_data=content)):
            assert detect_linux_distro() == "debian"

    def test_file_not_found_tries_distro_module(self):
        from navig.adapters.os.factory import detect_linux_distro

        mock_distro = MagicMock()
        mock_distro.id.return_value = "centos"

        with patch("builtins.open", side_effect=FileNotFoundError):
            with patch.dict("sys.modules", {"distro": mock_distro}):
                result = detect_linux_distro()
        assert result == "centos"

    def test_file_not_found_and_no_distro_module_returns_none(self):
        from navig.adapters.os.factory import detect_linux_distro

        with patch("builtins.open", side_effect=FileNotFoundError):
            with patch.dict("sys.modules", {"distro": None}):
                result = detect_linux_distro()
        assert result is None

    def test_no_id_line_uses_distro_fallback(self):
        from navig.adapters.os.factory import detect_linux_distro

        content = 'NAME="Arch Linux"\nVERSION_ID="rolling"\n'
        mock_distro = MagicMock()
        mock_distro.id.return_value = "arch"

        with patch("builtins.open", mock_open(read_data=content)):
            with patch.dict("sys.modules", {"distro": mock_distro}):
                result = detect_linux_distro()
        # No ID= line found → falls through to distro module
        assert result == "arch"


# ---------------------------------------------------------------------------
# get_os_adapter
# ---------------------------------------------------------------------------


class TestGetOsAdapter:
    def _mock_adapter(self, name):
        m = MagicMock()
        m.__name__ = name
        return m

    def test_windows_adapter_returned(self):
        from navig.adapters.os.factory import get_os_adapter

        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        with patch("navig.adapters.os.factory.detect_os", return_value="windows"):
            with patch("navig.adapters.os.windows.WindowsAdapter", mock_cls, create=True):
                with patch.dict(
                    "sys.modules",
                    {"navig.adapters.os.windows": MagicMock(WindowsAdapter=mock_cls)},
                ):
                    result = get_os_adapter()
        assert result is mock_instance

    def test_macos_adapter_returned(self):
        from navig.adapters.os.factory import get_os_adapter

        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        with patch.dict(
            "sys.modules",
            {"navig.adapters.os.macos": MagicMock(MacOSAdapter=mock_cls)},
        ):
            result = get_os_adapter("macos")
        assert result is mock_instance

    def test_darwin_alias_maps_to_macos(self):
        from navig.adapters.os.factory import get_os_adapter

        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        with patch.dict(
            "sys.modules",
            {"navig.adapters.os.macos": MagicMock(MacOSAdapter=mock_cls)},
        ):
            result = get_os_adapter("darwin")
        assert result is mock_instance

    def test_linux_adapter_returned(self):
        from navig.adapters.os.factory import get_os_adapter

        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        with patch("navig.adapters.os.factory.detect_linux_distro", return_value="ubuntu"):
            with patch.dict(
                "sys.modules",
                {"navig.adapters.os.linux": MagicMock(LinuxAdapter=mock_cls)},
            ):
                result = get_os_adapter("linux")
        assert result is mock_instance
        mock_cls.assert_called_once_with(distro="ubuntu")

    def test_unsupported_os_raises_value_error(self):
        from navig.adapters.os.factory import get_os_adapter

        with pytest.raises(ValueError, match="Unsupported"):
            get_os_adapter("plan9")

    def test_none_auto_detects(self):
        from navig.adapters.os.factory import get_os_adapter

        mock_cls = MagicMock()
        mock_cls.return_value = MagicMock()

        with patch("navig.adapters.os.factory.detect_os", return_value="macos"):
            with patch.dict(
                "sys.modules",
                {"navig.adapters.os.macos": MagicMock(MacOSAdapter=mock_cls)},
            ):
                get_os_adapter()  # no exception
        mock_cls.assert_called_once()


# ---------------------------------------------------------------------------
# get_os_adapter_for_remote
# ---------------------------------------------------------------------------


class TestGetOsAdapterForRemote:
    def _stub_get_os_adapter(self, factory_module):
        mock_result = MagicMock()
        mock_get = MagicMock(return_value=mock_result)
        return mock_get, mock_result

    def test_linux_string_maps_to_linux(self):
        from navig.adapters.os import factory as fac

        mock_get = MagicMock(return_value=MagicMock())
        with patch.object(fac, "get_os_adapter", mock_get):
            fac.get_os_adapter_for_remote("Linux 5.4.0-generic")
        mock_get.assert_called_once_with("linux")

    def test_darwin_maps_to_macos(self):
        from navig.adapters.os import factory as fac

        mock_get = MagicMock(return_value=MagicMock())
        with patch.object(fac, "get_os_adapter", mock_get):
            fac.get_os_adapter_for_remote("Darwin 21.4.0")
        mock_get.assert_called_once_with("macos")

    def test_windows_string_maps_to_windows(self):
        from navig.adapters.os import factory as fac

        mock_get = MagicMock(return_value=MagicMock())
        with patch.object(fac, "get_os_adapter", mock_get):
            fac.get_os_adapter_for_remote("Windows NT 10.0")
        mock_get.assert_called_once_with("windows")

    def test_mingw_maps_to_windows(self):
        from navig.adapters.os import factory as fac

        mock_get = MagicMock(return_value=MagicMock())
        with patch.object(fac, "get_os_adapter", mock_get):
            fac.get_os_adapter_for_remote("MINGW64_NT-10.0")
        mock_get.assert_called_once_with("windows")

    def test_msys_maps_to_windows(self):
        from navig.adapters.os import factory as fac

        mock_get = MagicMock(return_value=MagicMock())
        with patch.object(fac, "get_os_adapter", mock_get):
            fac.get_os_adapter_for_remote("MSYS_NT-10.0")
        mock_get.assert_called_once_with("windows")

    def test_unknown_os_info_falls_back_to_linux(self):
        from navig.adapters.os import factory as fac

        mock_get = MagicMock(return_value=MagicMock())
        with patch.object(fac, "get_os_adapter", mock_get):
            fac.get_os_adapter_for_remote("SunOS 5.11")
        mock_get.assert_called_once_with("linux")
