"""Tests for navig/tui/widgets/ — status_row, summary_panel, check_row, step_indicator, brand_hero.
Textual stub is installed by conftest.py in this directory.
"""
from __future__ import annotations
from types import SimpleNamespace
import pytest


def _badge(**kw):
    d = dict(status="ok", label="Service", detail="running", deep_link=None,
             color="green", symbol=u"\u2713")
    d.update(kw)
    return SimpleNamespace(**d)


class TestStatusRow:
    def _row(self, **kw):
        from navig.tui.widgets.status_row import StatusRow
        row = StatusRow.__new__(StatusRow)
        row._badge = _badge(**kw)
        return row

    def test_badge_property_returns_badge(self):
        row = self._row()
        assert row.badge is row._badge

    def test_deep_link_none(self):
        row = self._row(deep_link=None)
        assert row.deep_link is None

    def test_deep_link_string(self):
        row = self._row(deep_link="host")
        assert row.deep_link == "host"

    def test_update_badge_replaces_badge(self):
        row = self._row(status="ok")
        new = _badge(status="error")
        row.update_badge(new)
        assert row._badge.status == "error"

    def test_create_ok_badge(self):
        row = self._row(status="ok")
        assert row._badge.status == "ok"

    def test_create_error_badge(self):
        row = self._row(status="error")
        assert row._badge.status == "error"


class TestSummaryPanel:
    def _panel(self, **kw):
        from navig.tui.widgets.summary_panel import SummaryPanel
        d = dict(host="prod", app="myapp", user="root", status="ok",
                 profile_name="alice", ai_provider="openai",
                 local_runtime_enabled=True,
                 capability_packs=["core", "devops"],
                 shell_integration=True, git_hooks=False, telemetry=True)
        d.update(kw)
        panel = SummaryPanel.__new__(SummaryPanel)
        panel._cfg = SimpleNamespace(**d)
        panel._status = d["status"]
        return panel

    def test_render_is_string(self):
        panel = self._panel()
        result = panel.render()
        assert isinstance(result, str)

    def test_render_contains_profile(self):
        panel = self._panel(profile_name="alice")
        assert "alice" in panel.render()

    def test_render_contains_provider(self):
        panel = self._panel(ai_provider="openai")
        assert "openai" in panel.render()

    def test_set_status_updates(self):
        panel = self._panel(status="ok")
        panel.set_status("error")
        assert panel._status == "error"

    def test_refresh_from_updates_cfg(self):
        panel = self._panel(host="old")
        new_cfg = SimpleNamespace(host="new", app="new2", user="u", status="warn",
                                   profile_name="x", ai_provider="openai",
                                   local_runtime_enabled=True,
                                   capability_packs=["core"],
                                   shell_integration=True, git_hooks=False, telemetry=False)
        panel.refresh_from(new_cfg)
        assert panel._cfg.host == "new"


class TestCheckRow:
    def _row(self):
        from navig.tui.widgets.check_row import CheckRow
        row = CheckRow.__new__(CheckRow)
        row._label = "Test Check"
        row._state = "pending"
        row._hint = ""
        return row

    def test_set_pending(self):
        row = self._row()
        row.set_pending()
        assert row._state == "pending"

    def test_set_pass(self):
        row = self._row()
        row.set_pass()
        assert row._state == "pass"

    def test_set_fail_state(self):
        row = self._row()
        row.set_fail("bad config")
        assert row._state == "fail"

    def test_set_fail_hint(self):
        row = self._row()
        row.set_fail("missing key")
        assert row._hint == "missing key"

    def test_set_pass_clears_hint(self):
        row = self._row()
        row._hint = "old error"
        row._state = "fail"
        row.set_pass()
        assert row._hint == ""

    def test_set_fail_default_hint(self):
        row = self._row()
        row.set_fail()
        assert row._state == "fail"


class TestStepIndicator:
    def _ind(self, current=1, total=3, labels=None):
        from navig.tui.widgets.step_indicator import StepIndicator
        ind = StepIndicator.__new__(StepIndicator)
        ind.current_step = current
        ind.total_steps = total
        ind.step_labels = labels or []
        return ind

    def test_render_is_string(self):
        result = self._ind().render()
        assert isinstance(result, str)

    def test_render_not_empty(self):
        result = self._ind(1, 3).render()
        assert len(result) > 0

    def test_render_with_labels(self):
        result = self._ind(1, 2, ["Setup", "Done"]).render()
        assert result is not None

    def test_step_1_of_3(self):
        result = self._ind(1, 3).render()
        assert isinstance(result, str)

    def test_last_step(self):
        result = self._ind(3, 3).render()
        assert isinstance(result, str)


class TestBrandHero:
    def _hero(self):
        from navig.tui.widgets.brand_hero import BrandHero
        h = BrandHero.__new__(BrandHero)
        h._content = ""
        return h

    def test_initial_render_empty(self):
        assert self._hero().render() == ""

    def test_set_text_stores(self):
        h = self._hero()
        h.set_text("NAVIG")
        assert h._content == "NAVIG"

    def test_render_after_set(self):
        h = self._hero()
        h.set_text("hello")
        assert h.render() == "hello"

    def test_set_text_empty(self):
        h = self._hero()
        h.set_text("")
        assert h._content == ""

    def test_render_returns_str(self):
        assert isinstance(self._hero().render(), str)
