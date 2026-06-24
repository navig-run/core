"""Tests for navig/tui/widgets/summary_panel.py."""
from __future__ import annotations
from types import SimpleNamespace
import pytest

def _cfg(**kw):
    d = dict(host="prod", app="app", user="u", status="ok", profile_name="alice",
             ai_provider="openai", local_runtime_enabled=True, capability_packs=["core"],
             shell_integration=True, git_hooks=False, telemetry=True)
    d.update(kw); return SimpleNamespace(**d)

class TestSummaryPanel:
    def _panel(self, **kw):
        from navig.tui.widgets.summary_panel import SummaryPanel
        p = SummaryPanel.__new__(SummaryPanel); p._cfg = _cfg(**kw); p._status = "ok"; return p
    def test_render_string(self): assert isinstance(self._panel().render(), str)
    def test_render_has_provider(self): assert "openai" in self._panel().render()
    def test_render_has_profile(self): assert "alice" in self._panel().render()
    def test_set_status(self): p = self._panel(); p.set_status("error"); assert p._status == "error"
    def test_refresh_from(self):
        p = self._panel(); p.refresh_from(_cfg(profile_name="bob")); assert p._cfg.profile_name == "bob"
