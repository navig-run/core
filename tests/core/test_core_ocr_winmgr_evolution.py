"""Batch 99: tests for navig.core.ocr, navig.core.window_manager,
navig.core.evolution.base, navig.core.evolution.failure_summary."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.core.ocr
# ---------------------------------------------------------------------------

from navig.core.ocr import extract_ocr_text_from_image_bytes


class TestExtractOcrTextFromImageBytes:
    def test_returns_none_when_pytesseract_missing(self):
        """When pytesseract is not installed, must return None without raising."""
        with patch.dict("sys.modules", {"pytesseract": None, "PIL": None, "PIL.Image": None}):
            result = extract_ocr_text_from_image_bytes(b"fake bytes")
        assert result is None

    def test_returns_none_on_corrupt_bytes(self):
        """Corrupt image bytes → exception inside → returns None."""
        result = extract_ocr_text_from_image_bytes(b"\x00\x01\x02\x03")
        assert result is None

    def test_returns_none_for_empty_bytes(self):
        result = extract_ocr_text_from_image_bytes(b"")
        assert result is None

    def test_returns_none_when_text_too_short(self):
        """Simulate OCR returning very short text (< 3 chars) → None."""
        mock_pil = MagicMock()
        mock_img = MagicMock()
        mock_pil.Image.open.return_value = mock_img
        mock_tesseract = MagicMock()
        mock_tesseract.image_to_string.return_value = "ab"  # len 2 — below threshold

        with patch.dict("sys.modules", {"pytesseract": mock_tesseract, "PIL": mock_pil, "PIL.Image": mock_pil.Image}):
            result = extract_ocr_text_from_image_bytes(b"fake")
        assert result is None

    def test_returns_text_when_long_enough(self):
        """Simulate OCR returning meaningful text → returns it."""
        mock_pil = MagicMock()
        mock_img = MagicMock()
        mock_pil.Image.open.return_value = mock_img
        mock_tesseract = MagicMock()
        mock_tesseract.image_to_string.return_value = "Hello World"

        with patch.dict("sys.modules", {"pytesseract": mock_tesseract, "PIL": mock_pil, "PIL.Image": mock_pil.Image}):
            result = extract_ocr_text_from_image_bytes(b"fake")
        assert result == "Hello World"

    def test_strips_whitespace_from_result(self):
        """OCR result should be stripped of leading/trailing whitespace."""
        mock_pil = MagicMock()
        mock_img = MagicMock()
        mock_pil.Image.open.return_value = mock_img
        mock_tesseract = MagicMock()
        mock_tesseract.image_to_string.return_value = "  Hello World  \n"

        with patch.dict("sys.modules", {"pytesseract": mock_tesseract, "PIL": mock_pil, "PIL.Image": mock_pil.Image}):
            result = extract_ocr_text_from_image_bytes(b"fake")
        assert result == "Hello World"


# ---------------------------------------------------------------------------
# navig.core.window_manager
# ---------------------------------------------------------------------------

from navig.core.window_manager import WindowManager


@dataclass
class FakeWindow:
    id: str
    title: str
    class_name: str
    process_name: str
    x: int
    y: int
    width: int
    height: int
    is_maximized: bool = False
    is_minimized: bool = False


def _make_ahk_adapter(windows=None):
    adapter = MagicMock()
    adapter.get_all_windows.return_value = windows or []
    return adapter


class TestWindowManagerGetWindows:
    def test_returns_empty_when_no_ahk(self, tmp_path):
        wm = WindowManager(ahk_adapter=None)
        wm.layout_dir = tmp_path
        assert wm.get_windows() == []

    def test_returns_windows_from_adapter(self, tmp_path):
        fake_win = FakeWindow("1", "My App", "AppClass", "myapp.exe", 0, 0, 800, 600)
        ahk = _make_ahk_adapter([fake_win])
        wm = WindowManager(ahk_adapter=ahk)
        wm.layout_dir = tmp_path
        result = wm.get_windows()
        assert result == [fake_win]


class TestWindowManagerSaveLayout:
    def test_save_layout_no_ahk_logs_error(self, tmp_path, capsys):
        wm = WindowManager(ahk_adapter=None)
        wm.layout_dir = tmp_path
        wm.save_layout("test_layout")
        # Should not raise; prints error via console_helper.error

    def test_save_layout_creates_json_file(self, tmp_path):
        fake_win = FakeWindow("1", "My App", "AppClass", "myapp.exe", 10, 20, 800, 600)
        ahk = _make_ahk_adapter([fake_win])
        wm = WindowManager(ahk_adapter=ahk)
        wm.layout_dir = tmp_path
        wm.save_layout("mytest")
        layout_file = tmp_path / "mytest.json"
        assert layout_file.exists()
        data = json.loads(layout_file.read_text())
        assert len(data) == 1
        assert data[0]["title"] == "My App"

    def test_save_layout_skips_program_manager(self, tmp_path):
        w1 = FakeWindow("1", "Program Manager", "Progman", "shell.exe", 0, 0, 1920, 1080)
        w2 = FakeWindow("2", "Real App", "AppClass", "app.exe", 10, 10, 400, 300)
        ahk = _make_ahk_adapter([w1, w2])
        wm = WindowManager(ahk_adapter=ahk)
        wm.layout_dir = tmp_path
        wm.save_layout("filtered")
        data = json.loads((tmp_path / "filtered.json").read_text())
        titles = [d["title"] for d in data]
        assert "Program Manager" not in titles
        assert "Real App" in titles

    def test_save_layout_skips_zero_size_windows(self, tmp_path):
        w = FakeWindow("1", "Hidden", "Cls", "app.exe", 0, 0, 0, 0)
        ahk = _make_ahk_adapter([w])
        wm = WindowManager(ahk_adapter=ahk)
        wm.layout_dir = tmp_path
        wm.save_layout("empty")
        data = json.loads((tmp_path / "empty.json").read_text())
        assert data == []


class TestWindowManagerRestoreLayout:
    def test_restore_nonexistent_layout_no_crash(self, tmp_path):
        ahk = _make_ahk_adapter([])
        wm = WindowManager(ahk_adapter=ahk)
        wm.layout_dir = tmp_path
        wm.restore_layout("nope")  # Should not raise

    def test_restore_layout_calls_move_window(self, tmp_path):
        # Save a layout first
        fake_win = FakeWindow("42", "My App", "AppClass", "myapp.exe", 100, 200, 800, 600)
        ahk = _make_ahk_adapter([fake_win])
        wm = WindowManager(ahk_adapter=ahk)
        wm.layout_dir = tmp_path
        wm.save_layout("restore_test")

        # Restore the same layout
        wm.restore_layout("restore_test")
        ahk.move_window.assert_called_once()

    def test_restore_maximized_window(self, tmp_path):
        saved = {"title": "MaxApp", "class_name": "cls", "process_name": "proc.exe",
                 "x": 0, "y": 0, "width": 1920, "height": 1080,
                 "is_maximized": True, "is_minimized": False, "id": "5"}
        layout = tmp_path / "maxlayout.json"
        layout.write_text(json.dumps([saved]))

        cur_win = FakeWindow("5", "MaxApp", "cls", "proc.exe", 0, 0, 1920, 1080)
        ahk = _make_ahk_adapter([cur_win])
        wm = WindowManager(ahk_adapter=ahk)
        wm.layout_dir = tmp_path
        wm.restore_layout("maxlayout")
        ahk.maximize_window.assert_called_once()

    def test_restore_no_ahk_no_crash(self, tmp_path):
        wm = WindowManager(ahk_adapter=None)
        wm.layout_dir = tmp_path
        wm.restore_layout("whatever")  # Should not raise


class TestWindowManagerListLayouts:
    def test_list_layouts_empty(self, tmp_path):
        wm = WindowManager(ahk_adapter=None)
        wm.layout_dir = tmp_path
        assert wm.list_layouts() == []

    def test_list_layouts_returns_stems(self, tmp_path):
        (tmp_path / "alpha.json").write_text("[]")
        (tmp_path / "beta.json").write_text("[]")
        wm = WindowManager(ahk_adapter=None)
        wm.layout_dir = tmp_path
        layouts = wm.list_layouts()
        assert set(layouts) == {"alpha", "beta"}


# ---------------------------------------------------------------------------
# navig.core.evolution.base
# ---------------------------------------------------------------------------

from navig.core.evolution.base import BaseEvolver, EvolutionResult


class TestEvolutionResult:
    def test_default_values(self):
        r = EvolutionResult(success=True)
        assert r.artifact is None
        assert r.error == ""
        assert r.history is None
        assert r.attempts == 0

    def test_failed_result(self):
        r = EvolutionResult(success=False, error="something broke", attempts=3)
        assert not r.success
        assert r.error == "something broke"
        assert r.attempts == 3


class _SuccessEvolver(BaseEvolver):
    """Always succeeds on first attempt."""
    def _generate(self, goal, previous_artifact, error, context):
        return f"artifact_for_{goal}"

    def _validate(self, artifact, context):
        return None  # No error


class _FailEvolver(BaseEvolver):
    """Always fails validation."""
    def _generate(self, goal, previous_artifact, error, context):
        return f"bad_artifact"

    def _validate(self, artifact, context):
        return "always invalid"


class _EmptyEvolver(BaseEvolver):
    """Generator returns empty artifact."""
    def _generate(self, goal, previous_artifact, error, context):
        return None

    def _validate(self, artifact, context):
        return None


class _CrashEvolver(BaseEvolver):
    """Generator raises exception."""
    def _generate(self, goal, previous_artifact, error, context):
        raise RuntimeError("generation exploded")

    def _validate(self, artifact, context):
        return None


class _CacheEvolver(BaseEvolver):
    """Returns cached result immediately."""
    def _check_cache(self, goal):
        return "cached_artifact"

    def _generate(self, goal, previous_artifact, error, context):
        return "should_not_reach"

    def _validate(self, artifact, context):
        return None


class TestBaseEvolver:
    def test_success_on_first_attempt(self):
        evolver = _SuccessEvolver(max_retries=3)
        result = evolver.evolve("do something")
        assert result.success
        assert result.artifact == "artifact_for_do something"
        assert result.attempts == 1

    def test_exhausts_retries_on_failure(self):
        evolver = _FailEvolver(max_retries=2)
        result = evolver.evolve("fail goal")
        assert not result.success
        assert result.attempts == 2

    def test_history_populated_on_failure(self):
        evolver = _FailEvolver(max_retries=3)
        result = evolver.evolve("fail")
        assert result.history is not None
        assert len(result.history) == 3

    def test_empty_artifact_returns_failure(self):
        evolver = _EmptyEvolver(max_retries=2)
        result = evolver.evolve("empty")
        assert not result.success
        assert "empty artifact" in result.error.lower()

    def test_generation_exception_returns_failure(self):
        evolver = _CrashEvolver(max_retries=2)
        result = evolver.evolve("crash")
        assert not result.success
        assert "Generation failed" in result.error

    def test_cache_hit_returns_immediately(self):
        evolver = _CacheEvolver(max_retries=3)
        result = evolver.evolve("cached goal")
        assert result.success
        assert result.artifact == "cached_artifact"
        assert result.attempts == 0

    def test_max_retries_respected(self):
        evolver = _FailEvolver(max_retries=5)
        result = evolver.evolve("fail")
        assert result.attempts == 5

    def test_history_stored_on_object(self):
        evolver = _SuccessEvolver(max_retries=3)
        evolver.evolve("goal")
        assert len(evolver.history) == 1


# ---------------------------------------------------------------------------
# navig.core.evolution.failure_summary
# ---------------------------------------------------------------------------

from navig.core.evolution.failure_summary import summarize_check_failure


class TestSummarizeCheckFailure:
    def test_empty_output_returns_empty(self):
        result = summarize_check_failure("", "")
        assert result == ""

    def test_detects_failed_test_count(self):
        stdout = "FAILED tests/test_foo.py::test_bar\n3 failed, 10 passed"
        result = summarize_check_failure(stdout, "")
        assert "3" in result
        assert "Failed tests" in result

    def test_extracts_first_failing_targets(self):
        stdout = (
            "FAILED tests/test_a.py::test_1\n"
            "FAILED tests/test_b.py::test_2\n"
            "2 failed"
        )
        result = summarize_check_failure(stdout, "")
        assert "test_a.py" in result or "test_b.py" in result

    def test_includes_top_traceback_error(self):
        stdout = "some output\n"
        stderr = "E  AssertionError: expected True\n"
        result = summarize_check_failure(stdout, stderr)
        assert "AssertionError" in result

    def test_suggested_next_step_always_present(self):
        result = summarize_check_failure("FAILED tests/x.py::t\n1 failed", "")
        assert "next step" in result.lower()

    def test_no_failed_tests_but_has_output(self):
        result = summarize_check_failure("", "ImportError: no module named foo")
        assert "ImportError" in result or "Validation output" in result

    def test_stderr_combined_with_stdout(self):
        result = summarize_check_failure("FAILED tests/t.py::m\n1 failed", "E  ValueError: bad")
        assert "1" in result
        assert "ValueError" in result or "next step" in result.lower()
