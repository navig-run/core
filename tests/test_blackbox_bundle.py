import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.blackbox.bundle import (
    _default_log_files,
    create_bundle,
    inspect_bundle,
    write_bundle,
)
from navig.blackbox.types import BlackboxEvent, Bundle, EventType


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def dummy_bundle():
    return Bundle(
        id="test1234",
        created_at=None,  # will mock or let it fail
        navig_version="1.0.0",
        events=[BlackboxEvent.create(EventType.SYSTEM, {"test": "data"})],
        crash_reports=[{"timestamp": "2024-01-01", "error": "test"}],
        log_tails={"test.log": "test line 1\ntest line 2"},
        manifest_hash="hash123",
        sealed=False,
    )


def test_create_bundle(tmp_dir):
    with (
        patch("navig.blackbox.recorder.get_recorder") as mock_rec,
        patch("navig.blackbox.crash.list_crashes") as mock_crashes,
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.read_text", return_value="line1\nline2"),
    ):
        mock_rec.return_value.read_events.return_value = [
            BlackboxEvent.create(EventType.COMMAND, {"a": 1})
        ]

        crash_mock = MagicMock()
        crash_mock.to_dict.return_value = {"error": "test"}
        mock_crashes.return_value = [crash_mock]

        # Test default creation
        bundle = create_bundle(blackbox_dir=tmp_dir, log_files=[Path("dummy.log")])

        assert bundle.event_count() == 1
        assert bundle.crash_count() == 1
        assert "dummy.log" in bundle.log_tails
        assert bundle.log_tails["dummy.log"] == "line1\nline2"
        assert bundle.manifest_hash != ""


def test_create_bundle_defaults():
    with (
        patch("navig.blackbox.bundle._default_log_files", return_value=[]),
        patch("navig.blackbox.recorder.get_recorder") as mock_rec,
        patch("navig.blackbox.crash.list_crashes", return_value=[]),
        patch("navig.platform.paths.blackbox_dir", return_value=Path("/tmp")),
    ):
        mock_rec.return_value.read_events.return_value = []
        bundle = create_bundle()
        assert bundle.event_count() == 0


def test_write_and_inspect_bundle(tmp_dir, dummy_bundle):
    from datetime import datetime

    dummy_bundle.created_at = datetime.now()

    out_path = tmp_dir / "test_out.navbox"

    # Write
    written_path = write_bundle(dummy_bundle, out_path)
    assert written_path.exists()
    assert written_path.suffix == ".navbox"

    # Inspect
    loaded = inspect_bundle(written_path)
    assert loaded.id == "test1234"
    assert loaded.event_count() == 1
    assert loaded.crash_count() == 1
    assert loaded.events[0].event_type == EventType.SYSTEM
    assert loaded.crash_reports[0]["error"] == "test"
    assert "test.log" in loaded.log_tails
    assert "test line 1" in loaded.log_tails["test.log"]


def test_inspect_bundle_corrupt_entries(tmp_dir):
    out_path = tmp_dir / "corrupt.navbox"

    with zipfile.ZipFile(out_path, "w") as zf:
        zf.writestr("events.jsonl", "invalid json\n")
        zf.writestr("crashes/crash-1.json", "invalid json")
        zf.writestr("logs/test.log", "log line")

    loaded = inspect_bundle(out_path)
    assert loaded.event_count() == 0
    assert loaded.crash_count() == 0
    assert loaded.log_tails["test.log"] == "log line"


def test_default_log_files():
    with patch("navig.platform.paths.log_dir", return_value=Path("/tmp/logs")):
        res = _default_log_files()
        assert len(res) == 3
        paths = [p.name for p in res]
        assert "navig.log" in paths
        assert "debug.log" in paths
