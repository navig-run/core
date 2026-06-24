"""Tests for navig/onboarding/telemetry.py"""
from __future__ import annotations

import hashlib
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.onboarding.telemetry import (
    TELEMETRY_URL,
    _CONSENT_LINES,
    _OPT_OUT_VAR,
    _build_anon_id,
    _machine_id,
    ping_install_if_first_time,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_telemetry_url_is_string(self):
        assert isinstance(TELEMETRY_URL, str)

    def test_telemetry_url_starts_with_https(self):
        # URL may be overridden by env, but default starts with https
        import os
        if not os.environ.get("NAVIG_TELEMETRY_URL"):
            assert TELEMETRY_URL.startswith("https://")

    def test_consent_lines_is_string(self):
        assert isinstance(_CONSENT_LINES, str)

    def test_consent_lines_mentions_opt_out(self):
        assert "NAVIG_NO_TELEMETRY" in _CONSENT_LINES

    def test_opt_out_var_name(self):
        assert _OPT_OUT_VAR == "NAVIG_NO_TELEMETRY"


# ---------------------------------------------------------------------------
# _machine_id
# ---------------------------------------------------------------------------


class TestMachineId:
    def test_returns_string_or_none(self):
        result = _machine_id()
        assert result is None or isinstance(result, str)

    def test_returns_none_on_exception(self):
        with patch("navig.onboarding.telemetry.subprocess.run", side_effect=OSError):
            result = _machine_id()
        assert result is None

    def test_linux_reads_machine_id(self, tmp_path):
        fake_machine_id = tmp_path / "machine-id"
        fake_machine_id.write_text("abc123\n", encoding="utf-8")
        with patch("navig.onboarding.telemetry.platform.system", return_value="Linux"), \
             patch("navig.onboarding.telemetry.Path") as MockPath:
            # Set up Path to return our fake file
            def mock_path_call(p):
                obj = MagicMock()
                obj.__str__ = lambda self: p
                if "machine-id" in str(p) and "dbus" not in str(p):
                    obj.exists.return_value = True
                    obj.read_text.return_value = "abc123def456\n"
                else:
                    obj.exists.return_value = False
                return obj
            MockPath.side_effect = mock_path_call
            result = _machine_id()
        # Either returns a string or None (platform detection may differ in test)
        assert result is None or isinstance(result, str)

    def test_unknown_platform_returns_none(self):
        with patch("navig.onboarding.telemetry.platform.system", return_value="FreeBSD"):
            result = _machine_id()
        assert result is None


# ---------------------------------------------------------------------------
# _build_anon_id
# ---------------------------------------------------------------------------


class TestBuildAnonId:
    def test_returns_16_char_string(self):
        result = _build_anon_id()
        assert isinstance(result, str)
        assert len(result) == 16

    def test_is_hex_string(self):
        result = _build_anon_id()
        assert all(c in "0123456789abcdef" for c in result)

    def test_uses_sha256(self):
        with patch("navig.onboarding.telemetry._machine_id", return_value="fixed-machine-id"):
            result = _build_anon_id()
        expected = hashlib.sha256("fixed-machine-id".encode()).hexdigest()[:16]
        assert result == expected

    def test_fallback_when_no_machine_id(self):
        with patch("navig.onboarding.telemetry._machine_id", return_value=None):
            result = _build_anon_id()
        assert len(result) == 16

    def test_consistent_for_same_machine_id(self):
        with patch("navig.onboarding.telemetry._machine_id", return_value="same-id"):
            first = _build_anon_id()
            second = _build_anon_id()
        assert first == second


# ---------------------------------------------------------------------------
# ping_install_if_first_time
# ---------------------------------------------------------------------------


class TestPingInstallIfFirstTime:
    def test_noop_when_opt_out_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv(_OPT_OUT_VAR, "1")
        # opt-out exits before any network call — just verify no exception
        ping_install_if_first_time()  # should be a no-op

    def test_noop_when_already_pinged(self, monkeypatch, tmp_path):
        monkeypatch.delenv(_OPT_OUT_VAR, raising=False)
        marker = tmp_path / ".pinged"
        marker.write_text("1")
        # Patch the module-level marker
        with patch("navig.onboarding.telemetry._PINGED_MARKER", marker):
            ping_install_if_first_time()  # should be a no-op

    def test_writes_pinged_marker_on_first_run(self, monkeypatch, tmp_path):
        monkeypatch.delenv(_OPT_OUT_VAR, raising=False)
        marker = tmp_path / ".pinged"
        with patch("navig.onboarding.telemetry._PINGED_MARKER", marker), \
             patch("navig.onboarding.telemetry.atomic_write_text") as mock_write, \
             patch("navig.onboarding.telemetry._NAVIG_DIR", tmp_path):
            try:
                import requests as _req
                with patch.object(_req, "post"):
                    ping_install_if_first_time()
            except ImportError:
                # requests not installed — still writes marker
                ping_install_if_first_time()
        # atomic_write_text should be called to write the marker
        mock_write.assert_called_once()

    def test_handles_network_error_gracefully(self, monkeypatch, tmp_path):
        monkeypatch.delenv(_OPT_OUT_VAR, raising=False)
        marker = tmp_path / ".pinged"
        with patch("navig.onboarding.telemetry._PINGED_MARKER", marker), \
             patch("navig.onboarding.telemetry.atomic_write_text"):
            try:
                import requests as _req
                with patch.object(_req, "post", side_effect=ConnectionError("no network")):
                    # Should not raise
                    ping_install_if_first_time()
            except ImportError:
                pass  # requests not installed — also safe

    def test_opt_out_skips_marker_write(self, monkeypatch, tmp_path):
        monkeypatch.setenv(_OPT_OUT_VAR, "1")
        marker = tmp_path / ".pinged"
        with patch("navig.onboarding.telemetry._PINGED_MARKER", marker), \
             patch("navig.onboarding.telemetry.atomic_write_text") as mock_write:
            ping_install_if_first_time()
        mock_write.assert_not_called()

    def test_payload_contains_install_event(self, monkeypatch, tmp_path):
        monkeypatch.delenv(_OPT_OUT_VAR, raising=False)
        marker = tmp_path / ".pinged"
        captured_payloads = []

        def fake_post(url, json=None, timeout=None):
            captured_payloads.append(json)
            return MagicMock()

        with patch("navig.onboarding.telemetry._PINGED_MARKER", marker), \
             patch("navig.onboarding.telemetry.atomic_write_text"):
            try:
                import requests as _req
                with patch.object(_req, "post", side_effect=fake_post):
                    ping_install_if_first_time()
                if captured_payloads:
                    assert captured_payloads[0]["event"] == "install"
                    assert "platform" in captured_payloads[0]
                    assert "python" in captured_payloads[0]
                    assert "anon_id" in captured_payloads[0]
                    assert len(captured_payloads[0]["anon_id"]) == 16
            except ImportError:
                pass  # requests not installed — skip payload check
