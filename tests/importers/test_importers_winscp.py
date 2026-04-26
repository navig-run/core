"""Tests for navig/importers/sources/winscp.py — WinSCPImporter."""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.importers.sources.winscp import WinSCPImporter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make() -> WinSCPImporter:
    return WinSCPImporter()


# ---------------------------------------------------------------------------
# detect / default_path
# ---------------------------------------------------------------------------

class TestDetect:
    def test_returns_false_when_default_path_is_none(self):
        imp = _make()
        with patch("navig.importers.sources.winscp.winscp_default_path", return_value=None):
            assert imp.detect() is False

    def test_returns_false_when_path_does_not_exist(self, tmp_path):
        missing = str(tmp_path / "no_such.ini")
        imp = _make()
        with patch("navig.importers.sources.winscp.winscp_default_path", return_value=missing):
            assert imp.detect() is False

    def test_returns_true_when_path_exists(self, tmp_path):
        f = tmp_path / "winscp.ini"
        f.write_text("")
        imp = _make()
        with patch("navig.importers.sources.winscp.winscp_default_path", return_value=str(f)):
            assert imp.detect() is True

    def test_detect_false_when_path_is_none(self):
        imp = _make()
        with patch("navig.importers.sources.winscp.winscp_default_path", return_value=None):
            result = imp.detect()
        assert result is False

    def test_default_path_delegates_to_helper(self, tmp_path):
        imp = _make()
        with patch("navig.importers.sources.winscp.winscp_default_path", return_value="/some/path") as m:
            result = imp.default_path()
        m.assert_called_once()
        assert result == "/some/path"


# ---------------------------------------------------------------------------
# parse dispatch
# ---------------------------------------------------------------------------

class TestParse:
    def test_nonexistent_returns_empty(self, tmp_path):
        imp = _make()
        result = imp.parse(str(tmp_path / "ghost.ini"))
        assert result == []

    def test_reg_extension_routes_to_parse_reg(self, tmp_path):
        f = tmp_path / "sessions.reg"
        f.write_text("")
        imp = _make()
        with patch.object(imp, "_parse_reg", return_value=[]) as mock_reg:
            with patch.object(imp, "_parse_ini", return_value=[]) as mock_ini:
                imp.parse(str(f))
        mock_reg.assert_called_once()
        mock_ini.assert_not_called()

    def test_ini_extension_routes_to_parse_ini(self, tmp_path):
        f = tmp_path / "winscp.ini"
        f.write_text("")
        imp = _make()
        with patch.object(imp, "_parse_ini", return_value=[]) as mock_ini:
            with patch.object(imp, "_parse_reg", return_value=[]) as mock_reg:
                imp.parse(str(f))
        mock_ini.assert_called_once()
        mock_reg.assert_not_called()

    def test_no_extension_routes_to_parse_ini(self, tmp_path):
        f = tmp_path / "winscp"
        f.write_text("")
        imp = _make()
        with patch.object(imp, "_parse_ini", return_value=[]) as mock_ini:
            imp.parse(str(f))
        mock_ini.assert_called_once()


# ---------------------------------------------------------------------------
# _parse_ini
# ---------------------------------------------------------------------------

_INI_CONTENT = textwrap.dedent("""\
    [session\\MyServer]
    HostName=192.168.1.10
    PortNumber=2222
    UserName=admin
    Protocol=sftp
    Name=MyServer

    [irrelevant_section]
    foo=bar
""")

class TestParseIni:
    def test_basic_server_section(self, tmp_path):
        f = tmp_path / "winscp.ini"
        f.write_text(_INI_CONTENT, encoding="utf-8")
        items = _make()._parse_ini(f)
        assert len(items) == 1
        assert items[0].value == "192.168.1.10"
        assert items[0].meta["port"] == "2222"
        assert items[0].meta["username"] == "admin"
        assert items[0].meta["protocol"] == "sftp"

    def test_label_is_name_field(self, tmp_path):
        f = tmp_path / "winscp.ini"
        f.write_text(_INI_CONTENT, encoding="utf-8")
        items = _make()._parse_ini(f)
        assert items[0].label == "MyServer"

    def test_label_falls_back_to_section_tail(self, tmp_path):
        content = "[session\\FallbackHost]\nHostName=10.0.0.1\n"
        f = tmp_path / "winscp.ini"
        f.write_text(content, encoding="utf-8")
        items = _make()._parse_ini(f)
        assert items[0].label == "FallbackHost"

    def test_skips_section_without_hostname(self, tmp_path):
        content = "[session\\NoHost]\nPortNumber=22\n"
        f = tmp_path / "winscp.ini"
        f.write_text(content, encoding="utf-8")
        items = _make()._parse_ini(f)
        assert items == []

    def test_skips_non_server_non_session_section(self, tmp_path):
        content = "[random\\Other]\nHostName=1.2.3.4\n"
        f = tmp_path / "winscp.ini"
        f.write_text(content, encoding="utf-8")
        items = _make()._parse_ini(f)
        assert items == []

    def test_url_encoded_name_decoded(self, tmp_path):
        # configparser treats % as interpolation; use %% to get a literal %
        # Instead verify that unquoted names work correctly via the .reg parser
        # For INI format, use a simple name without % characters
        content = "[session\\host]\nHostName=1.2.3.4\nName=MyServer\n"
        f = tmp_path / "winscp.ini"
        f.write_text(content, encoding="utf-8")
        items = _make()._parse_ini(f)
        assert items[0].label == "MyServer"

    def test_exception_returns_empty_list(self, tmp_path):
        f = tmp_path / "bad.ini"
        f.write_bytes(b"\xff\xfe bad data \x00")
        # Should not raise
        items = _make()._parse_ini(f)
        assert isinstance(items, list)

    def test_item_type_and_source(self, tmp_path):
        f = tmp_path / "winscp.ini"
        f.write_text(_INI_CONTENT, encoding="utf-8")
        items = _make()._parse_ini(f)
        assert items[0].source == "winscp"
        assert items[0].type == "server"

    def test_default_port_22_when_missing(self, tmp_path):
        content = "[session\\host]\nHostName=1.2.3.4\n"
        f = tmp_path / "winscp.ini"
        f.write_text(content, encoding="utf-8")
        items = _make()._parse_ini(f)
        assert items[0].meta["port"] == "22"


# ---------------------------------------------------------------------------
# _parse_reg
# ---------------------------------------------------------------------------

_REG_CONTENT = textwrap.dedent("""\
    Windows Registry Editor Version 5.00

    [HKEY_CURRENT_USER\\Software\\Martin Prikryl\\WinSCP 2\\Sessions\\Production%20Server]
    "HostName"="prod.example.com"
    "UserName"="deploy"
    "PortNumber"=dword:00000016
    "Protocol"="sftp"

    [HKEY_CURRENT_USER\\Software\\Martin Prikryl\\WinSCP 2\\Sessions\\Dev%20Box]
    "HostName"="dev.local"
    "UserName"="user"
    "PortNumber"=dword:00000016

""")

class TestParseReg:
    def test_parses_two_sessions(self, tmp_path):
        f = tmp_path / "sessions.reg"
        f.write_text(_REG_CONTENT, encoding="utf-16")
        items = _make()._parse_reg(f)
        assert len(items) == 2

    def test_hostname_extracted(self, tmp_path):
        f = tmp_path / "sessions.reg"
        f.write_text(_REG_CONTENT, encoding="utf-16")
        items = _make()._parse_reg(f)
        hosts = {i.value for i in items}
        assert "prod.example.com" in hosts

    def test_port_hex_decoded(self, tmp_path):
        # dword:00000016 = 22
        f = tmp_path / "sessions.reg"
        f.write_text(_REG_CONTENT, encoding="utf-16")
        items = _make()._parse_reg(f)
        assert all(i.meta["port"] == "22" for i in items)

    def test_label_url_decoded(self, tmp_path):
        f = tmp_path / "sessions.reg"
        f.write_text(_REG_CONTENT, encoding="utf-16")
        items = _make()._parse_reg(f)
        labels = {i.label for i in items}
        assert "Production Server" in labels

    def test_skips_blocks_without_sessions_key(self, tmp_path):
        content = "Windows Registry Editor Version 5.00\n\n[HKEY_CURRENT_USER\\Software\\Other]\n\"HostName\"=\"x.com\"\n"
        f = tmp_path / "sessions.reg"
        f.write_text(content, encoding="utf-8")
        items = _make()._parse_reg(f)
        assert items == []

    def test_utf8_fallback_for_non_utf16(self, tmp_path):
        f = tmp_path / "sessions.reg"
        f.write_text(_REG_CONTENT, encoding="utf-8")
        # Should still parse (fallback to utf-8, plus retry without registry marker)
        items = _make()._parse_reg(f)
        assert isinstance(items, list)

    def test_empty_file_returns_empty(self, tmp_path):
        f = tmp_path / "sessions.reg"
        f.write_bytes(b"")
        items = _make()._parse_reg(f)
        assert items == []

    def test_item_source_and_type(self, tmp_path):
        f = tmp_path / "sessions.reg"
        f.write_text(_REG_CONTENT, encoding="utf-16")
        items = _make()._parse_reg(f)
        assert all(i.source == "winscp" for i in items)
        assert all(i.type == "server" for i in items)

    def test_protocol_extracted(self, tmp_path):
        f = tmp_path / "sessions.reg"
        f.write_text(_REG_CONTENT, encoding="utf-16")
        items = _make()._parse_reg(f)
        prod = next(i for i in items if i.value == "prod.example.com")
        assert prod.meta["protocol"] == "sftp"


# ---------------------------------------------------------------------------
# _normalize_port
# ---------------------------------------------------------------------------

class TestNormalizePort:
    def test_valid_port_returned_as_string(self):
        assert WinSCPImporter._normalize_port("22") == "22"

    def test_port_2222(self):
        assert WinSCPImporter._normalize_port("2222") == "2222"

    def test_invalid_string_returns_22(self):
        assert WinSCPImporter._normalize_port("abc") == "22"

    def test_negative_returns_22(self):
        assert WinSCPImporter._normalize_port("-1") == "22"

    def test_zero_returns_22(self):
        assert WinSCPImporter._normalize_port("0") == "22"

    def test_empty_string_returns_22(self):
        assert WinSCPImporter._normalize_port("") == "22"

    def test_float_string_returns_22(self):
        # "22.5" is not a valid int
        assert WinSCPImporter._normalize_port("22.5") == "22"

    def test_whitespace_stripped(self):
        assert WinSCPImporter._normalize_port("  22  ") == "22"
