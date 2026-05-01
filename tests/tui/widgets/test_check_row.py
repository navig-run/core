"""Tests for navig/tui/widgets/check_row.py."""
from __future__ import annotations
import pytest

class TestCheckRow:
    def _row(self):
        from navig.tui.widgets.check_row import CheckRow
        r = CheckRow.__new__(CheckRow); r._label = "SSL"; r._state = "pending"; r._hint = ""; return r
    def test_set_pass(self): r = self._row(); r.set_pass(); assert r._state == "pass"
    def test_set_fail_state(self): r = self._row(); r.set_fail("err"); assert r._state == "fail"
    def test_set_fail_hint(self): r = self._row(); r.set_fail("bad"); assert r._hint == "bad"
    def test_set_pending(self): r = self._row(); r.set_pass(); r.set_pending(); assert r._state == "pending"
    def test_set_pass_clears_hint(self): r = self._row(); r._hint = "x"; r.set_pass(); assert r._hint == ""
