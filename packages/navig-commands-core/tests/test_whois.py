"""Tests for navig-commands-core/commands/whois.py"""
from __future__ import annotations

import json
import pathlib
import sys
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "commands"))
from whois import _is_ip, handle


class TestIsIp:
    def test_ipv4_detected(self):
        assert _is_ip("1.2.3.4") is True

    def test_ipv6_detected(self):
        assert _is_ip("2001:db8::1") is True

    def test_domain_not_ip(self):
        assert _is_ip("example.com") is False

    def test_empty_string_not_ip(self):
        assert _is_ip("") is False

    def test_partial_ip_not_ip(self):
        assert _is_ip("1.2.3") is False


class TestArgValidation:
    def test_missing_target_returns_error(self):
        result = handle({})
        assert result["status"] == "error"
        assert "target" in result["message"].lower()

    def test_empty_target_returns_error(self):
        result = handle({"target": "   "})
        assert result["status"] == "error"


class TestDomainLookup:
    def _make_domain_response(self):
        return {
            "ldhName": "example.com",
            "status": ["active"],
            "nameservers": [
                {"ldhName": "ns1.example.com"},
                {"ldhName": "ns2.example.com"},
            ],
            "events": [
                {"eventAction": "registration", "eventDate": "1995-08-14"},
                {"eventAction": "expiration", "eventDate": "2030-08-13"},
            ],
            "entities": [
                {
                    "roles": ["registrar"],
                    "vcardArray": [
                        "vcard",
                        [["fn", {}, "text", "ACME Registrar"]],
                    ],
                }
            ],
        }

    def test_domain_lookup_returns_ok(self):
        payload = json.dumps(self._make_domain_response()).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = payload
        with patch("whois.urlopen", return_value=mock_resp):
            result = handle({"target": "example.com"})
        assert result["status"] == "ok"
        data = result["data"]
        assert data["name"] == "example.com"
        assert "ns1.example.com" in data["nameservers"]
        assert data["registered"] == "1995-08-14"
        assert data["expires"] == "2030-08-13"
        assert len(data["entities"]) == 1
        assert data["entities"][0]["roles"] == ["registrar"]

    def test_domain_url_used(self):
        payload = json.dumps(self._make_domain_response()).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = payload
        with patch("whois.urlopen", return_value=mock_resp) as mock_open:
            handle({"target": "example.com"})
        called_url = mock_open.call_args[0][0].full_url
        assert "rdap.org/domain/example.com" in called_url

    def test_empty_nameservers(self):
        data = self._make_domain_response()
        data["nameservers"] = []
        payload = json.dumps(data).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = payload
        with patch("whois.urlopen", return_value=mock_resp):
            result = handle({"target": "example.com"})
        assert result["data"]["nameservers"] == []


class TestIpLookup:
    def _make_ip_response(self):
        return {
            "ipVersion": "v4",
            "handle": "1.2.3.0 - 1.2.3.255",
            "country": "US",
            "name": "ACME-BLOCK",
            "type": "ASSIGNED",
        }

    def test_ip_lookup_returns_ok(self):
        payload = json.dumps(self._make_ip_response()).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = payload
        with patch("whois.urlopen", return_value=mock_resp):
            result = handle({"target": "1.2.3.4"})
        assert result["status"] == "ok"
        data = result["data"]
        assert data["ip_version"] == "v4"
        assert data["country"] == "US"
        assert data["name"] == "ACME-BLOCK"

    def test_ip_url_used(self):
        payload = json.dumps(self._make_ip_response()).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = payload
        with patch("whois.urlopen", return_value=mock_resp) as mock_open:
            handle({"target": "8.8.8.8"})
        called_url = mock_open.call_args[0][0].full_url
        assert "rdap.org/ip/8.8.8.8" in called_url


class TestErrorHandling:
    def test_url_error_returns_error(self):
        with patch("whois.urlopen", side_effect=URLError("network down")):
            result = handle({"target": "example.com"})
        assert result["status"] == "error"
        assert "RDAP lookup failed" in result["message"]
        assert result["target"] == "example.com"

    def test_generic_exception_returns_error(self):
        with patch("whois.urlopen", side_effect=ValueError("bad json")):
            result = handle({"target": "example.com"})
        assert result["status"] == "error"
        assert result["target"] == "example.com"

    def test_custom_timeout_passed(self):
        with patch("whois.urlopen", side_effect=URLError("x")) as mock_open:
            handle({"target": "example.com", "timeout": "5.0"})
        # urlopen called with timeout=5.0 as keyword arg
        assert mock_open.call_args.kwargs.get("timeout") == 5.0
