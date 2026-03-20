"""
tests.test_telemetry — Unit tests for navig.onboarding.telemetry.

Coverage:
  - NAVIG_NO_TELEMETRY=1 silences ping entirely (no network, no marker)
  - .pinged marker prevents repeated pings
  - anon_id is exactly 16 hex chars
  - _machine_id() never raises on any supported platform
  - network failure is silently swallowed
  - marker is written regardless of network success/failure
"""
from __future__ import annotations

import hashlib
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────

def _reload_module():
    """Force a fresh import so module-level constants are re-evaluated."""
    import navig.onboarding.telemetry as t
    importlib.reload(t)
    return t


# ── Tests ─────────────────────────────────────────────────────────────────


class TestOptOut:
    def test_env_var_silences_everything(self, tmp_path, monkeypatch):
        """NAVIG_NO_TELEMETRY=1 must short-circuit before writing any file."""
        monkeypatch.setenv("NAVIG_NO_TELEMETRY", "1")
        marker = tmp_path / ".pinged"

        import navig.onboarding.telemetry as t
        with patch.object(t, "_PINGED_MARKER", marker):
            t.ping_install_if_first_time()

        assert not marker.exists(), "Marker must NOT be written when opted out"

    def test_opt_out_does_not_call_requests(self, monkeypatch):
        monkeypatch.setenv("NAVIG_NO_TELEMETRY", "1")

        import navig.onboarding.telemetry as t
        mock_requests = MagicMock()
        with patch.dict(sys.modules, {"requests": mock_requests}):
            t.ping_install_if_first_time()

        mock_requests.post.assert_not_called()


class TestMarker:
    def test_existing_marker_prevents_ping(self, tmp_path, monkeypatch):
        """If .pinged already exists, no network call must be made."""
        monkeypatch.delenv("NAVIG_NO_TELEMETRY", raising=False)
        marker = tmp_path / ".pinged"
        marker.touch()

        import navig.onboarding.telemetry as t
        mock_requests = MagicMock()
        with (
            patch.object(t, "_PINGED_MARKER", marker),
            patch.dict(sys.modules, {"requests": mock_requests}),
        ):
            t.ping_install_if_first_time()

        mock_requests.post.assert_not_called()

    def test_marker_written_on_success(self, tmp_path, monkeypatch):
        """Marker must be written after a successful network ping."""
        monkeypatch.delenv("NAVIG_NO_TELEMETRY", raising=False)
        marker = tmp_path / ".pinged"

        import navig.onboarding.telemetry as t
        mock_requests = MagicMock()
        mock_requests.post.return_value.status_code = 200

        with (
            patch.object(t, "_PINGED_MARKER", marker),
            patch.dict(sys.modules, {"requests": mock_requests}),
        ):
            t.ping_install_if_first_time()

        assert marker.exists(), "Marker must be written after successful ping"

    def test_marker_written_on_network_failure(self, tmp_path, monkeypatch):
        """Marker must be written even when the network call fails."""
        monkeypatch.delenv("NAVIG_NO_TELEMETRY", raising=False)
        marker = tmp_path / ".pinged"

        import navig.onboarding.telemetry as t
        mock_requests = MagicMock()
        mock_requests.post.side_effect = ConnectionError("unreachable")

        with (
            patch.object(t, "_PINGED_MARKER", marker),
            patch.dict(sys.modules, {"requests": mock_requests}),
        ):
            t.ping_install_if_first_time()

        assert marker.exists(), "Marker must be written even after network failure"


class TestAnonId:
    def test_anon_id_length(self):
        """anon_id must be exactly 16 hex characters."""
        import navig.onboarding.telemetry as t
        anon_id = t._build_anon_id()
        assert len(anon_id) == 16, f"Expected 16 chars, got {len(anon_id)}"

    def test_anon_id_is_hex(self):
        """anon_id must be a valid hex string."""
        import navig.onboarding.telemetry as t
        anon_id = t._build_anon_id()
        assert all(c in "0123456789abcdef" for c in anon_id), f"Non-hex chars in: {anon_id}"

    def test_anon_id_is_deterministic(self):
        """Same machine should produce the same anon_id across calls."""
        import navig.onboarding.telemetry as t
        assert t._build_anon_id() == t._build_anon_id()

    def test_anon_id_fallback_when_machine_id_unavailable(self, monkeypatch):
        """Should still produce a valid 16-char ID when machine_id returns None."""
        import navig.onboarding.telemetry as t
        with patch.object(t, "_machine_id", return_value=None):
            anon_id = t._build_anon_id()
        assert len(anon_id) == 16
        assert all(c in "0123456789abcdef" for c in anon_id)


class TestMachineId:
    def test_machine_id_never_raises(self):
        """_machine_id() must never raise, regardless of platform failures."""
        import navig.onboarding.telemetry as t
        # Should return a string or None, never an exception
        result = t._machine_id()
        assert result is None or isinstance(result, str)

    def test_machine_id_subprocess_failure_returns_none(self):
        """If the subprocess command fails, should return None gracefully."""
        import navig.onboarding.telemetry as t

        with patch("subprocess.run", side_effect=FileNotFoundError("wmic not found")):
            result = t._machine_id()
        assert result is None

    def test_machine_id_timeout_returns_none(self):
        """Subprocess timeout should be handled without raising."""
        import navig.onboarding.telemetry as t
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("wmic", 5)):
            result = t._machine_id()
        assert result is None


class TestNetworkFailure:
    def test_requests_import_error_swallowed(self, tmp_path, monkeypatch):
        """Missing 'requests' library must not crash the installer."""
        monkeypatch.delenv("NAVIG_NO_TELEMETRY", raising=False)
        marker = tmp_path / ".pinged"

        import navig.onboarding.telemetry as t
        # Remove requests from sys.modules and mock ImportError
        with (
            patch.object(t, "_PINGED_MARKER", marker),
            patch.dict(sys.modules, {"requests": None}),
        ):
            # Should not raise even if requests is unavailable
            try:
                t.ping_install_if_first_time()
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"ping_install_if_first_time raised unexpectedly: {exc}")

    def test_connection_error_swallowed(self, tmp_path, monkeypatch):
        """ConnectionError during the POST must be silently swallowed."""
        monkeypatch.delenv("NAVIG_NO_TELEMETRY", raising=False)
        marker = tmp_path / ".pinged"

        import navig.onboarding.telemetry as t
        mock_requests = MagicMock()
        mock_requests.post.side_effect = ConnectionError("no internet")

        with (
            patch.object(t, "_PINGED_MARKER", marker),
            patch.dict(sys.modules, {"requests": mock_requests}),
        ):
            try:
                t.ping_install_if_first_time()
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"Exception leaked from ping: {exc}")
