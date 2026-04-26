"""Tests for navig/blackbox/bundle.py — write_bundle, inspect_bundle, create_bundle."""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.blackbox.bundle import (
    _BUNDLE_EXT,
    _LOG_TAIL_LINES,
    inspect_bundle,
    write_bundle,
)
from navig.blackbox.types import BlackboxEvent, Bundle, EventType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(etype=EventType.COMMAND, payload=None):
    return BlackboxEvent.create(etype, payload or {"cmd": "ls"})


def _make_bundle(events=None, crash_reports=None, log_tails=None):
    return Bundle(
        id="abc12345",
        created_at=datetime.now(timezone.utc),
        navig_version="1.0.0",
        events=events or [],
        crash_reports=crash_reports or [],
        log_tails=log_tails or {},
        manifest_hash="deadbeef",
        sealed=False,
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_bundle_extension(self):
        assert _BUNDLE_EXT == ".navbox"

    def test_log_tail_lines_positive(self):
        assert _LOG_TAIL_LINES > 0


# ---------------------------------------------------------------------------
# write_bundle
# ---------------------------------------------------------------------------


class TestWriteBundle:
    def test_adds_navbox_extension_if_missing(self, tmp_path):
        bundle = _make_bundle()
        out = write_bundle(bundle, tmp_path / "myreport")
        assert out.suffix == ".navbox"

    def test_keeps_navbox_extension_if_present(self, tmp_path):
        bundle = _make_bundle()
        out = write_bundle(bundle, tmp_path / "myreport.navbox")
        assert out.name == "myreport.navbox"

    def test_creates_valid_zip(self, tmp_path):
        bundle = _make_bundle()
        out = write_bundle(bundle, tmp_path / "bundle")
        assert zipfile.is_zipfile(out)

    def test_zip_contains_manifest(self, tmp_path):
        bundle = _make_bundle()
        out = write_bundle(bundle, tmp_path / "bundle")
        with zipfile.ZipFile(out) as zf:
            assert "manifest.json" in zf.namelist()

    def test_zip_contains_events_jsonl(self, tmp_path):
        bundle = _make_bundle(events=[_make_event()])
        out = write_bundle(bundle, tmp_path / "bundle")
        with zipfile.ZipFile(out) as zf:
            assert "events.jsonl" in zf.namelist()

    def test_manifest_contains_bundle_id(self, tmp_path):
        bundle = _make_bundle()
        out = write_bundle(bundle, tmp_path / "bundle")
        with zipfile.ZipFile(out) as zf:
            manifest = json.loads(zf.read("manifest.json").decode())
        assert manifest["bundle_id"] == bundle.id

    def test_manifest_contains_event_count(self, tmp_path):
        bundle = _make_bundle(events=[_make_event(), _make_event()])
        out = write_bundle(bundle, tmp_path / "bundle")
        with zipfile.ZipFile(out) as zf:
            manifest = json.loads(zf.read("manifest.json").decode())
        assert manifest["event_count"] == 2

    def test_crash_reports_written(self, tmp_path):
        crash = {"timestamp": "2024-01-01T12:00:00", "type": "ValueError"}
        bundle = _make_bundle(crash_reports=[crash])
        out = write_bundle(bundle, tmp_path / "bundle")
        with zipfile.ZipFile(out) as zf:
            crash_files = [n for n in zf.namelist() if n.startswith("crashes/")]
        assert len(crash_files) == 1

    def test_log_tails_written(self, tmp_path):
        bundle = _make_bundle(log_tails={"debug.log": "line1\nline2"})
        out = write_bundle(bundle, tmp_path / "bundle")
        with zipfile.ZipFile(out) as zf:
            assert "logs/debug.log" in zf.namelist()

    def test_creates_parent_directories(self, tmp_path):
        bundle = _make_bundle()
        nested = tmp_path / "a" / "b" / "c" / "bundle"
        out = write_bundle(bundle, nested)
        assert out.exists()

    def test_returns_output_path(self, tmp_path):
        bundle = _make_bundle()
        out = write_bundle(bundle, tmp_path / "bundle")
        assert out.exists()
        assert isinstance(out, Path)


# ---------------------------------------------------------------------------
# inspect_bundle
# ---------------------------------------------------------------------------


class TestInspectBundle:
    def _write_and_inspect(self, bundle, tmp_path):
        out = write_bundle(bundle, tmp_path / "bundle")
        return inspect_bundle(out)

    def test_roundtrip_id(self, tmp_path):
        bundle = _make_bundle()
        result = self._write_and_inspect(bundle, tmp_path)
        assert result.id == bundle.id

    def test_roundtrip_navig_version(self, tmp_path):
        bundle = _make_bundle()
        result = self._write_and_inspect(bundle, tmp_path)
        assert result.navig_version == bundle.navig_version

    def test_roundtrip_events(self, tmp_path):
        bundle = _make_bundle(events=[_make_event(), _make_event()])
        result = self._write_and_inspect(bundle, tmp_path)
        assert result.event_count() == 2

    def test_roundtrip_crash_reports(self, tmp_path):
        crash = {"timestamp": "2024-01-01T12:00:00", "type": "ValueError"}
        bundle = _make_bundle(crash_reports=[crash])
        result = self._write_and_inspect(bundle, tmp_path)
        assert result.crash_count() == 1

    def test_roundtrip_log_tails(self, tmp_path):
        bundle = _make_bundle(log_tails={"debug.log": "line1\nline2"})
        result = self._write_and_inspect(bundle, tmp_path)
        assert "debug.log" in result.log_tails

    def test_roundtrip_sealed_false(self, tmp_path):
        bundle = _make_bundle()
        result = self._write_and_inspect(bundle, tmp_path)
        assert result.sealed is False

    def test_handles_missing_manifest_gracefully(self, tmp_path):
        # Write a zip without manifest.json
        zpath = tmp_path / "empty.navbox"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("events.jsonl", "")
        result = inspect_bundle(zpath)
        assert result.id == "unknown"

    def test_handles_malformed_event_line(self, tmp_path):
        bundle = _make_bundle()
        out = write_bundle(bundle, tmp_path / "bundle")
        # Corrupt the events.jsonl
        with zipfile.ZipFile(out, "a") as zf:
            zf.writestr("events.jsonl", "not-json\n")
        # Should not raise
        result = inspect_bundle(out)
        assert isinstance(result, Bundle)


# ---------------------------------------------------------------------------
# create_bundle
# ---------------------------------------------------------------------------


class TestCreateBundle:
    def test_returns_bundle(self, tmp_path):
        mock_recorder = MagicMock()
        mock_recorder.read_events.return_value = []
        with patch("navig.blackbox.recorder.get_recorder", return_value=mock_recorder), \
             patch("navig.blackbox.crash.list_crashes", return_value=[]):
            from navig.blackbox.bundle import create_bundle
            result = create_bundle(blackbox_dir=tmp_path)
        assert isinstance(result, Bundle)

    def test_bundle_not_sealed_by_default(self, tmp_path):
        mock_recorder = MagicMock()
        mock_recorder.read_events.return_value = []
        with patch("navig.blackbox.recorder.get_recorder", return_value=mock_recorder), \
             patch("navig.blackbox.crash.list_crashes", return_value=[]):
            from navig.blackbox.bundle import create_bundle
            result = create_bundle(blackbox_dir=tmp_path)
        assert result.sealed is False

    def test_bundle_has_manifest_hash(self, tmp_path):
        mock_recorder = MagicMock()
        mock_recorder.read_events.return_value = []
        with patch("navig.blackbox.recorder.get_recorder", return_value=mock_recorder), \
             patch("navig.blackbox.crash.list_crashes", return_value=[]):
            from navig.blackbox.bundle import create_bundle
            result = create_bundle(blackbox_dir=tmp_path)
        assert result.manifest_hash

    def test_includes_existing_log_tails(self, tmp_path):
        log_file = tmp_path / "debug.log"
        log_file.write_text("line a\nline b\n")
        mock_recorder = MagicMock()
        mock_recorder.read_events.return_value = []
        with patch("navig.blackbox.recorder.get_recorder", return_value=mock_recorder), \
             patch("navig.blackbox.crash.list_crashes", return_value=[]):
            from navig.blackbox.bundle import create_bundle
            result = create_bundle(blackbox_dir=tmp_path, log_files=[log_file])
        assert "debug.log" in result.log_tails
