"""Tests for navig-commands-core/commands/ping.py"""
from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "commands"))
from ping import handle


class TestArgValidation:
    def test_missing_host_returns_error(self):
        result = handle({})
        assert result["status"] == "error"
        assert "host" in result["message"].lower()

    def test_empty_host_returns_error(self):
        result = handle({"host": "   "})
        assert result["status"] == "error"

    def test_default_port_is_80(self):
        with patch("ping.socket.getaddrinfo") as mock_gai, \
             patch("ping.socket.create_connection"):
            mock_gai.return_value = [(None, None, None, None, ("1.2.3.4", 80))]
            result = handle({"host": "example.com"})
        assert result["status"] == "ok"
        assert result["data"]["port"] == 80

    def test_custom_port_used(self):
        with patch("ping.socket.getaddrinfo") as mock_gai, \
             patch("ping.socket.create_connection"):
            mock_gai.return_value = [(None, None, None, None, ("1.2.3.4", 443))]
            result = handle({"host": "example.com", "port": 443})
        assert result["data"]["port"] == 443

    def test_port_accepts_string_int(self):
        with patch("ping.socket.getaddrinfo") as mock_gai, \
             patch("ping.socket.create_connection"):
            mock_gai.return_value = [(None, None, None, None, ("1.2.3.4", 8080))]
            result = handle({"host": "example.com", "port": "8080"})
        assert result["data"]["port"] == 8080

    def test_timeout_accepts_string_float(self):
        with patch("ping.socket.getaddrinfo") as mock_gai, \
             patch("ping.socket.create_connection") as mock_cc:
            mock_gai.return_value = [(None, None, None, None, ("1.2.3.4", 80))]
            handle({"host": "example.com", "timeout": "1.5"})
        mock_cc.assert_called_once_with(("example.com", 80), timeout=1.5)


class TestDnsOnlyMode:
    def test_port_zero_returns_dns_result(self):
        with patch("ping.socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(None, None, None, None, ("9.9.9.9", 80))]
            result = handle({"host": "quad9.net", "port": 0})
        assert result["status"] == "ok"
        assert result["data"]["method"] == "dns"
        assert result["data"]["resolved"] == "9.9.9.9"

    def test_dns_failure_returns_error(self):
        with patch("ping.socket.getaddrinfo", side_effect=socket.gaierror("nxdomain")):
            result = handle({"host": "nxdomain.invalid"})
        assert result["status"] == "error"
        assert "DNS" in result["message"]
        assert result["host"] == "nxdomain.invalid"


class TestTcpConnect:
    def test_successful_tcp_returns_ok(self):
        with patch("ping.socket.getaddrinfo") as mock_gai, \
             patch("ping.socket.create_connection") as mock_cc:
            mock_gai.return_value = [(None, None, None, None, ("93.184.216.34", 80))]
            mock_cc.return_value.__enter__ = MagicMock(return_value=None)
            mock_cc.return_value.__exit__ = MagicMock(return_value=False)
            result = handle({"host": "example.com"})
        assert result["status"] == "ok"
        assert result["data"]["reachable"] is True
        assert result["data"]["method"] == "tcp"

    def test_connection_refused_returns_error(self):
        with patch("ping.socket.getaddrinfo") as mock_gai, \
             patch("ping.socket.create_connection", side_effect=ConnectionRefusedError("refused")):
            mock_gai.return_value = [(None, None, None, None, ("1.2.3.4", 9999))]
            result = handle({"host": "example.com", "port": 9999})
        assert result["status"] == "error"
        assert result["data"]["reachable"] is False
        assert result["data"]["host"] == "example.com"

    def test_timeout_returns_error(self):
        with patch("ping.socket.getaddrinfo") as mock_gai, \
             patch("ping.socket.create_connection", side_effect=TimeoutError("timed out")):
            mock_gai.return_value = [(None, None, None, None, ("1.2.3.4", 80))]
            result = handle({"host": "slow.example.com"})
        assert result["status"] == "error"
        assert result["data"]["reachable"] is False

    def test_oserror_returns_error(self):
        with patch("ping.socket.getaddrinfo") as mock_gai, \
             patch("ping.socket.create_connection", side_effect=OSError("network unreachable")):
            mock_gai.return_value = [(None, None, None, None, ("10.0.0.1", 80))]
            result = handle({"host": "unreachable.local"})
        assert result["status"] == "error"

    def test_resolved_ip_included_in_error_data(self):
        with patch("ping.socket.getaddrinfo") as mock_gai, \
             patch("ping.socket.create_connection", side_effect=ConnectionRefusedError()):
            mock_gai.return_value = [(None, None, None, None, ("5.5.5.5", 80))]
            result = handle({"host": "example.com"})
        assert result["data"]["resolved"] == "5.5.5.5"
