"""Hermetic unit tests for navig.providers.bridge_grid_reader."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import navig.providers.bridge_grid_reader as bgr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_record(age_seconds: float = 1.0) -> dict:
    """Build a record with ts within TTL."""
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {
        "pid": 99999,
        "ts": ts.isoformat(),
        "slot": 0,
        "app": "vscode",
        "role": "primary",
        "llm_port": 42070,
        "bridge_port": 42071,
    }


def _write_grid(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record), encoding="utf-8")


# ---------------------------------------------------------------------------
# PRIMARY_TTL_SECONDS
# ---------------------------------------------------------------------------


class TestConstants:
    def test_primary_ttl_is_positive(self):
        assert bgr.PRIMARY_TTL_SECONDS > 0

    def test_bridge_default_port(self):
        assert bgr.BRIDGE_DEFAULT_PORT == 42070


# ---------------------------------------------------------------------------
# _is_pid_alive
# ---------------------------------------------------------------------------


class TestIsPidAlive:
    def test_negative_pid_returns_false(self):
        assert bgr._is_pid_alive(-1) is False

    def test_zero_pid_returns_false(self):
        assert bgr._is_pid_alive(0) is False

    def test_alive_when_os_kill_succeeds(self):
        with patch.object(bgr.os, "kill", return_value=None):
            assert bgr._is_pid_alive(1234) is True

    def test_dead_when_os_kill_raises_oserror(self):
        with patch.object(bgr.os, "kill", side_effect=OSError("ESRCH")):
            assert bgr._is_pid_alive(1234) is False

    def test_dead_when_process_lookup_error(self):
        with patch.object(bgr.os, "kill", side_effect=ProcessLookupError):
            assert bgr._is_pid_alive(9999) is False


# ---------------------------------------------------------------------------
# _read_and_validate (using patched bridge_grid_path)
# ---------------------------------------------------------------------------


class TestReadAndValidate:
    def test_returns_none_when_file_missing(self, tmp_path):
        missing = tmp_path / "bridge-grid.json"
        with patch.object(bgr, "_bridge_grid_path", missing):
            assert bgr._read_and_validate() is None

    def test_returns_none_for_invalid_json(self, tmp_path):
        f = tmp_path / "bridge-grid.json"
        f.write_text("NOT JSON", encoding="utf-8")
        with patch.object(bgr, "_bridge_grid_path", f):
            assert bgr._read_and_validate() is None

    def test_returns_data_for_fresh_record(self, tmp_path):
        f = tmp_path / "bridge-grid.json"
        record = _fresh_record(age_seconds=1.0)
        _write_grid(f, record)
        with (
            patch.object(bgr, "_bridge_grid_path", f),
            patch.object(bgr, "_is_pid_alive", return_value=True),
        ):
            result = bgr._read_and_validate()
        assert result is not None
        assert result["llm_port"] == 42070

    def test_returns_none_for_expired_ts(self, tmp_path):
        f = tmp_path / "bridge-grid.json"
        record = _fresh_record(age_seconds=bgr.PRIMARY_TTL_SECONDS + 5)
        _write_grid(f, record)
        with patch.object(bgr, "_bridge_grid_path", f):
            assert bgr._read_and_validate() is None

    def test_returns_none_for_dead_pid(self, tmp_path):
        f = tmp_path / "bridge-grid.json"
        record = _fresh_record(age_seconds=1.0)
        _write_grid(f, record)
        with (
            patch.object(bgr, "_bridge_grid_path", f),
            patch.object(bgr, "_is_pid_alive", return_value=False),
        ):
            assert bgr._read_and_validate() is None

    def test_no_ts_field_still_returns_data(self, tmp_path):
        f = tmp_path / "bridge-grid.json"
        record = {"pid": 99999, "llm_port": 42070, "bridge_port": 42071}
        _write_grid(f, record)
        with patch.object(bgr, "_is_pid_alive", return_value=True), patch.object(bgr, "_bridge_grid_path", f):
            result = bgr._read_and_validate()
        assert result is not None

    def test_invalid_ts_returns_none(self, tmp_path):
        f = tmp_path / "bridge-grid.json"
        record = {"pid": 99999, "ts": "not-a-date", "llm_port": 42070}
        _write_grid(f, record)
        with patch.object(bgr, "_bridge_grid_path", f):
            assert bgr._read_and_validate() is None


# ---------------------------------------------------------------------------
# read_bridge_grid (caching)
# ---------------------------------------------------------------------------


class TestReadBridgeGrid:
    def test_force_bypasses_cache(self, tmp_path):
        f = tmp_path / "bridge-grid.json"
        record = _fresh_record(1.0)
        _write_grid(f, record)
        with (
            patch.object(bgr, "_bridge_grid_path", f),
            patch.object(bgr, "_is_pid_alive", return_value=True),
        ):
            bgr.invalidate_cache()
            r1 = bgr.read_bridge_grid(force=True)
            r2 = bgr.read_bridge_grid(force=True)
        assert r1 is not None
        assert r2 is not None


# ---------------------------------------------------------------------------
# invalidate_cache
# ---------------------------------------------------------------------------


class TestInvalidateCache:
    def test_invalidate_sets_last_read_to_zero(self):
        bgr.invalidate_cache()
        assert bgr._last_read_ts == 0.0


# ---------------------------------------------------------------------------
# is_bridge_grid_alive / get_llm_port / get_bridge_port
# ---------------------------------------------------------------------------


class TestConvenienceFunctions:
    def test_is_alive_false_when_no_file(self, tmp_path):
        missing = tmp_path / "no-grid.json"
        bgr.invalidate_cache()
        with patch.object(bgr, "_bridge_grid_path", missing):
            assert bgr.is_bridge_grid_alive(force=True) is False

    def test_get_llm_port_none_when_no_file(self, tmp_path):
        missing = tmp_path / "no-grid.json"
        bgr.invalidate_cache()
        with patch.object(bgr, "_bridge_grid_path", missing):
            assert bgr.get_llm_port(force=True) is None

    def test_get_bridge_port_none_when_no_file(self, tmp_path):
        missing = tmp_path / "no-grid.json"
        bgr.invalidate_cache()
        with patch.object(bgr, "_bridge_grid_path", missing):
            assert bgr.get_bridge_port(force=True) is None

    def test_is_alive_true_with_valid_record(self, tmp_path):
        f = tmp_path / "bridge-grid.json"
        record = _fresh_record(1.0)
        _write_grid(f, record)
        bgr.invalidate_cache()
        with (
            patch.object(bgr, "_bridge_grid_path", f),
            patch.object(bgr, "_is_pid_alive", return_value=True),
        ):
            assert bgr.is_bridge_grid_alive(force=True) is True

    def test_get_llm_port_returns_value(self, tmp_path):
        f = tmp_path / "bridge-grid.json"
        record = _fresh_record(1.0)
        _write_grid(f, record)
        bgr.invalidate_cache()
        with (
            patch.object(bgr, "_bridge_grid_path", f),
            patch.object(bgr, "_is_pid_alive", return_value=True),
        ):
            assert bgr.get_llm_port(force=True) == 42070

    def test_get_bridge_port_returns_value(self, tmp_path):
        f = tmp_path / "bridge-grid.json"
        record = _fresh_record(1.0)
        _write_grid(f, record)
        bgr.invalidate_cache()
        with (
            patch.object(bgr, "_bridge_grid_path", f),
            patch.object(bgr, "_is_pid_alive", return_value=True),
        ):
            assert bgr.get_bridge_port(force=True) == 42071
