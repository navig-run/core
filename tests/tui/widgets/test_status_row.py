"""Tests for navig/tui/widgets/status_row.py."""
from __future__ import annotations
from types import SimpleNamespace
import pytest

def _badge(**kw):
    d = dict(status="ok", label="Svc", detail="up", deep_link=None, color="green", symbol=u"\u2713")
    d.update(kw); return SimpleNamespace(**d)

class TestStatusRow:
    def _row(self, **kw):
        from navig.tui.widgets.status_row import StatusRow
        r = StatusRow.__new__(StatusRow); r._badge = _badge(**kw); return r
    def test_badge_ok(self): assert self._row().badge.status == "ok"
    def test_badge_error(self): assert self._row(status="error").badge.status == "error"
    def test_deep_link_none(self): assert self._row().deep_link is None
    def test_deep_link_value(self): assert self._row(deep_link="host").deep_link == "host"
    def test_update_badge(self):
        r = self._row(); r.update_badge(_badge(status="warn")); assert r._badge.status == "warn"
