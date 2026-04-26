"""Tests for navig.adapters.automation.types — ExecutionResult, WindowInfo."""
from __future__ import annotations

import pytest

from navig.adapters.automation.types import ExecutionResult, WindowInfo


class TestExecutionResult:
    def test_defaults(self):
        r = ExecutionResult(success=True)
        assert r.stdout == ""
        assert r.stderr == ""
        assert r.exit_code == 0
        assert r.duration_seconds == 0.0
        assert r.status == "COMPLETED"

    def test_failed_result(self):
        r = ExecutionResult(success=False, stderr="error msg", exit_code=1)
        assert r.success is False
        assert r.exit_code == 1

    def test_custom_status(self):
        r = ExecutionResult(success=True, status="SKIPPED")
        assert r.status == "SKIPPED"


class TestWindowInfo:
    def _make(self, **kw):
        defaults = dict(
            title="Notepad", id="0x1234", pid=100,
            class_name="Notepad", x=0, y=0, width=800, height=600
        )
        defaults.update(kw)
        return WindowInfo(**defaults)

    def test_basic_construction(self):
        w = self._make()
        assert w.title == "Notepad"
        assert w.pid == 100

    def test_to_dict_normal_state(self):
        d = self._make().to_dict()
        assert d["state"] == "normal"
        assert d["title"] == "Notepad"

    def test_to_dict_minimized(self):
        d = self._make(is_minimized=True).to_dict()
        assert d["state"] == "minimized"

    def test_to_dict_maximized(self):
        d = self._make(is_maximized=True).to_dict()
        assert d["state"] == "maximized"

    def test_to_dict_minimized_takes_priority(self):
        # Both set — minimized should win
        d = self._make(is_minimized=True, is_maximized=True).to_dict()
        assert d["state"] == "minimized"

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for key in ("title", "id", "pid", "class_name", "x", "y", "width", "height",
                    "process_name", "is_minimized", "is_maximized", "state"):
            assert key in d

    def test_process_name_defaults_none(self):
        w = self._make()
        assert w.process_name is None
