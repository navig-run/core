"""Tests for navig.platform.windows_utils."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from navig.platform.windows_utils import (
    check_pid_exists,
    ps_quote,
    ps_quote_for_xml,
    remove_private_use_chars,
    run_with_graceful_timeout,
)


# ─── ps_quote ─────────────────────────────────────────────────────────────────


def test_ps_quote_simple():
    assert ps_quote("hello") == "'hello'"


def test_ps_quote_escapes_single_quotes():
    assert ps_quote("it's") == "'it''s'"


def test_ps_quote_empty():
    assert ps_quote("") == "''"


def test_ps_quote_multiple_apostrophes():
    assert ps_quote("a'b'c") == "'a''b''c'"


# ─── ps_quote_for_xml ─────────────────────────────────────────────────────────


def test_ps_quote_for_xml_escapes_ampersand():
    result = ps_quote_for_xml("a & b")
    assert "&amp;" in result


def test_ps_quote_for_xml_escapes_angle_brackets():
    result = ps_quote_for_xml("<tag>")
    assert "&lt;" in result
    assert "&gt;" in result


def test_ps_quote_for_xml_wraps_in_ps_quotes():
    result = ps_quote_for_xml("hello")
    assert result.startswith("'")
    assert result.endswith("'")


# ─── remove_private_use_chars ─────────────────────────────────────────────────


def test_remove_private_use_chars_strips_pua():
    pua_char = "\uE001"
    assert remove_private_use_chars(f"hello{pua_char}world") == "helloworld"


def test_remove_private_use_chars_keeps_normal():
    assert remove_private_use_chars("Hello, World! 123") == "Hello, World! 123"


def test_remove_private_use_chars_empty():
    assert remove_private_use_chars("") == ""


# ─── check_pid_exists ─────────────────────────────────────────────────────────


def test_check_pid_exists_true():
    with patch("navig.platform.windows_utils.psutil") as m:
        m.pid_exists.return_value = True
        assert check_pid_exists(1234) is True


def test_check_pid_exists_false():
    with patch("navig.platform.windows_utils.psutil") as m:
        m.pid_exists.return_value = False
        assert check_pid_exists(9999) is False


def test_check_pid_exists_no_psutil(monkeypatch):
    monkeypatch.setattr("navig.platform.windows_utils.psutil", None)
    # Without psutil, falls back to os.kill check or returns False.
    result = check_pid_exists(1)
    assert isinstance(result, bool)


# ─── run_with_graceful_timeout ────────────────────────────────────────────────


def test_run_with_graceful_timeout_non_windows_calls_subprocess_run():
    with patch("navig.platform.windows_utils.sys") as mock_sys:
        mock_sys.platform = "linux"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
            run_with_graceful_timeout("echo hi", timeout=5)
            mock_run.assert_called_once()


def test_run_with_graceful_timeout_passes_popenargs():
    """On non-Windows the first positional arg is forwarded to subprocess.run."""
    with patch("navig.platform.windows_utils.sys") as mock_sys:
        mock_sys.platform = "linux"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
            run_with_graceful_timeout(["ls", "-la"], timeout=3)
            call_args = mock_run.call_args
            assert call_args[0][0] == ["ls", "-la"]
