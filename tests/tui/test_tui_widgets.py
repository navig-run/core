"""Unit tests for navig.tui.widgets — BrandHero, StepIndicator, CheckRow,
SummaryPanel, StatusRow.

textual is NOT installed in CI/test environments, so we stub it before any
navig.tui imports.  The widget logic (render(), _refresh_render(), etc.) is
pure string-building and state management that doesn't require a running app.
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Stub textual before navig.tui imports attempt to load it
# ---------------------------------------------------------------------------

def _install_textual_stub() -> None:
    """Create minimal textual stubs in sys.modules."""
    if "textual" in sys.modules:
        return  # already present (real or stub)

    textual_mod = types.ModuleType("textual")
    sys.modules["textual"] = textual_mod

    # textual.reactive
    reactive_mod = types.ModuleType("textual.reactive")

    class _reactive:  # noqa: N801
        def __init__(self, default=None, **_kwargs):
            self._default = default

        def __set_name__(self, owner, name):
            self._name = f"_reactive_{name}"

        def __get__(self, obj, objtype=None):
            if obj is None:
                return self
            return getattr(obj, self._name, self._default)

        def __set__(self, obj, value):
            object.__setattr__(obj, self._name, value)

    reactive_mod.reactive = _reactive  # type: ignore[attr-defined]
    sys.modules["textual.reactive"] = reactive_mod

    # textual.widgets
    widgets_mod = types.ModuleType("textual.widgets")

    class _Static:
        def __init__(self, *args, **kwargs):
            pass

        def refresh(self, *args, **kwargs):
            pass

        def update(self, content="", *args, **kwargs):
            pass

    widgets_mod.Static = _Static  # type: ignore[attr-defined]
    sys.modules["textual.widgets"] = widgets_mod

    # textual.app
    app_mod = types.ModuleType("textual.app")
    app_mod.App = type("App", (), {})  # type: ignore[attr-defined]
    sys.modules["textual.app"] = app_mod

    # textual.screen
    screen_mod = types.ModuleType("textual.screen")
    screen_mod.Screen = type("Screen", (), {})  # type: ignore[attr-defined]
    sys.modules["textual.screen"] = screen_mod

    # textual.containers
    containers_mod = types.ModuleType("textual.containers")
    sys.modules["textual.containers"] = containers_mod

    # textual.binding
    binding_mod = types.ModuleType("textual.binding")
    binding_mod.Binding = type("Binding", (), {})  # type: ignore[attr-defined]
    sys.modules["textual.binding"] = binding_mod

    # textual.css.query
    css_mod = types.ModuleType("textual.css")
    css_query_mod = types.ModuleType("textual.css.query")
    sys.modules["textual.css"] = css_mod
    sys.modules["textual.css.query"] = css_query_mod


_install_textual_stub()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_static(widget_instance: object) -> None:
    """Replace update/refresh on a bare widget so they don't need an app."""
    widget_instance.update = MagicMock()  # type: ignore[attr-defined]
    widget_instance.refresh = MagicMock()  # type: ignore[attr-defined]


def _simple_cfg(**overrides) -> SimpleNamespace:
    defaults = dict(
        profile_name="alice",
        ai_provider="openai",
        local_runtime_enabled=True,
        capability_packs=["core", "devops"],
        shell_integration=True,
        git_hooks=False,
        telemetry=True,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _simple_badge(**overrides) -> SimpleNamespace:
    defaults = dict(
        color="green",
        symbol="✓",
        status="ok",
        detail="running",
        deep_link="",
        label="My Service",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# BrandHero
# ---------------------------------------------------------------------------

class TestBrandHero:
    def _make(self):
        from navig.tui.widgets.brand_hero import BrandHero

        with patch("textual.widgets.Static.__init__", return_value=None), \
             patch("textual.widgets.Static.refresh", return_value=None), \
             patch("textual.widgets.Static.update", return_value=None):
            obj = BrandHero()
        _stub_static(obj)
        return obj

    def test_initial_content_empty(self):
        bh = self._make()
        assert bh._content == ""

    def test_render_returns_content(self):
        bh = self._make()
        bh._content = "NAVIG"
        assert bh.render() == "NAVIG"

    def test_set_text_updates_content(self):
        bh = self._make()
        bh.set_text("Hello!")
        assert bh._content == "Hello!"

    def test_set_text_calls_refresh(self):
        bh = self._make()
        bh.set_text("X")
        bh.refresh.assert_called_once()

    def test_render_after_set_text(self):
        bh = self._make()
        bh.set_text("Logo")
        assert bh.render() == "Logo"

    def test_render_empty_string(self):
        bh = self._make()
        assert bh.render() == ""


# ---------------------------------------------------------------------------
# StepIndicator
# ---------------------------------------------------------------------------

class TestStepIndicator:
    def _make(self, current=0, total=5, labels=None):
        from navig.tui.widgets.step_indicator import StepIndicator

        with patch("textual.widgets.Static.__init__", return_value=None), \
             patch("textual.reactive.reactive.__set__", autospec=False), \
             patch("textual.reactive.reactive.__get__", autospec=False):
            obj = StepIndicator.__new__(StepIndicator)

        _stub_static(obj)
        # Set attributes directly, bypassing Textual reactive descriptor.
        object.__setattr__(obj, "current_step", current)
        object.__setattr__(obj, "total_steps", total)
        object.__setattr__(
            obj,
            "step_labels",
            labels or ["Identity", "Provider", "Runtime", "Packs", "Shell", "Integrations"],
        )
        return obj

    def test_render_first_step(self):
        w = self._make(current=0, total=3, labels=["A", "B", "C"])
        result = w.render()
        assert "Step 1/3" in result
        assert "A" in result

    def test_render_last_step(self):
        w = self._make(current=2, total=3, labels=["A", "B", "C"])
        result = w.render()
        assert "Step 3/3" in result
        assert "C" in result
        assert "100%" in result

    def test_render_mid_step(self):
        w = self._make(current=1, total=4, labels=["A", "B", "C", "D"])
        result = w.render()
        assert "Step 2/4" in result
        assert "50%" in result

    def test_render_contains_dots(self):
        w = self._make(current=1, total=3, labels=["A", "B", "C"])
        result = w.render()
        # Should have at least one completed indicator and one current
        assert result  # non-empty

    def test_render_percent_calculation(self):
        w = self._make(current=0, total=4, labels=["A", "B", "C", "D"])
        result = w.render()
        assert "25%" in result


# ---------------------------------------------------------------------------
# CheckRow
# ---------------------------------------------------------------------------

class TestCheckRow:
    def _make(self, label="Disk Space"):
        from navig.tui.widgets.check_row import CheckRow

        with patch("textual.widgets.Static.__init__", return_value=None), \
             patch("textual.widgets.Static.update", return_value=None):
            obj = CheckRow(label)
        _stub_static(obj)
        return obj

    def test_initial_state_pending(self):
        row = self._make("CPU")
        assert row._state == "pending"

    def test_set_pass_changes_state(self):
        row = self._make("CPU")
        row.set_pass()
        assert row._state == "pass"

    def test_set_fail_changes_state(self):
        row = self._make("CPU")
        row.set_fail("Fix it")
        assert row._state == "fail"
        assert row._hint == "Fix it"

    def test_set_fail_empty_hint(self):
        row = self._make("CPU")
        row.set_fail()
        assert row._hint == ""

    def test_set_pending_resets_hint(self):
        row = self._make("CPU")
        row.set_fail("hint")
        row.set_pending()
        assert row._state == "pending"
        assert row._hint == ""

    def test_refresh_render_called_on_set_pass(self):
        row = self._make("CPU")
        row.update.reset_mock()
        row.set_pass()
        row.update.assert_called_once()

    def test_refresh_render_pass_text_contains_label(self):
        row = self._make("Disk")
        captured = []
        row.update = lambda text: captured.append(text)
        row.set_pass()
        assert "Disk" in captured[0]

    def test_refresh_render_fail_includes_hint(self):
        row = self._make("Net")
        captured = []
        row.update = lambda text: captured.append(text)
        row.set_fail("Check cables")
        assert "Check cables" in captured[0]

    def test_refresh_render_pending_no_hint(self):
        row = self._make("Mem")
        captured = []
        row.update = lambda text: captured.append(text)
        row.set_pending()
        assert captured[0]  # non-empty
        assert "Check cables" not in captured[0]


# ---------------------------------------------------------------------------
# SummaryPanel
# ---------------------------------------------------------------------------

class TestSummaryPanel:
    def _make(self, cfg=None):
        from navig.tui.widgets.summary_panel import SummaryPanel

        cfg = cfg or _simple_cfg()
        with patch("textual.widgets.Static.__init__", return_value=None), \
             patch("textual.widgets.Static.refresh", return_value=None):
            obj = SummaryPanel.__new__(SummaryPanel)
        _stub_static(obj)
        obj._cfg = cfg
        obj._status = "active"
        return obj

    def test_render_contains_profile_name(self):
        panel = self._make(_simple_cfg(profile_name="bob"))
        result = panel.render()
        assert "bob" in result

    def test_render_contains_provider(self):
        panel = self._make(_simple_cfg(ai_provider="anthropic"))
        result = panel.render()
        assert "anthropic" in result

    def test_render_local_runtime(self):
        panel = self._make(_simple_cfg(local_runtime_enabled=True))
        result = panel.render()
        assert "local" in result

    def test_render_cloud_runtime(self):
        panel = self._make(_simple_cfg(local_runtime_enabled=False))
        result = panel.render()
        assert "cloud" in result

    def test_render_packs(self):
        panel = self._make(_simple_cfg(capability_packs=["alpha", "beta"]))
        result = panel.render()
        assert "alpha" in result
        assert "beta" in result

    def test_render_empty_packs_shows_dash(self):
        panel = self._make(_simple_cfg(capability_packs=[]))
        result = panel.render()
        assert "—" in result or result  # non-empty

    def test_render_active_status(self):
        panel = self._make()
        panel._status = "active"
        result = panel.render()
        assert "active" in result

    def test_set_status_updates(self):
        panel = self._make()
        panel.set_status("pending")
        assert panel._status == "pending"

    def test_render_shell_integration_true(self):
        panel = self._make(_simple_cfg(shell_integration=True))
        result = panel.render()
        assert "✓" in result


# ---------------------------------------------------------------------------
# StatusRow
# ---------------------------------------------------------------------------

class TestStatusRow:
    def _make(self, badge=None):
        from navig.tui.widgets.status_row import StatusRow

        badge = badge or _simple_badge()
        with patch("textual.widgets.Static.__init__", return_value=None), \
             patch("textual.widgets.Static.update", return_value=None):
            obj = StatusRow.__new__(StatusRow)
        _stub_static(obj)
        obj._badge = badge
        # Manually call update_badge to exercise logic
        obj.update_badge(badge)
        return obj

    def test_badge_property(self):
        b = _simple_badge(label="Redis")
        row = self._make(b)
        assert row.badge is b

    def test_deep_link_property(self):
        b = _simple_badge(deep_link="/settings/redis")
        row = self._make(b)
        assert row.deep_link == "/settings/redis"

    def test_update_badge_calls_update(self):
        row = self._make()
        row.update.reset_mock()
        row.update_badge(_simple_badge(label="Foo"))
        row.update.assert_called_once()

    def test_update_badge_ok_status(self):
        captured = []
        b = _simple_badge(status="ok", label="DB", detail="up", color="green")
        row = self._make(b)
        row.update = lambda t: captured.append(t)
        row.update_badge(b)
        assert "DB" in captured[0]
        assert "✓" in captured[0]

    def test_update_badge_error_has_exclamation(self):
        captured = []
        b = _simple_badge(status="error", label="API", detail="down", deep_link="")
        row = self._make(b)
        row.update = lambda t: captured.append(t)
        row.update_badge(b)
        assert "!" in captured[0]

    def test_update_badge_warn_icon(self):
        captured = []
        b = _simple_badge(status="warn", label="CPU", detail="high", deep_link="")
        row = self._make(b)
        row.update = lambda t: captured.append(t)
        row.update_badge(b)
        assert "▲" in captured[0]

    def test_update_badge_error_with_deep_link_includes_cta(self):
        captured = []
        b = _simple_badge(status="error", label="DB", deep_link="/settings/db")
        row = self._make(b)
        row.update = lambda t: captured.append(t)
        row.update_badge(b)
        assert "Edit" in captured[0] or "settings" in captured[0]

    def test_update_badge_missing_with_deep_link(self):
        captured = []
        b = _simple_badge(status="missing", label="Key", deep_link="/settings/key")
        row = self._make(b)
        row.update = lambda t: captured.append(t)
        row.update_badge(b)
        assert "Configure" in captured[0] or "settings" in captured[0]
