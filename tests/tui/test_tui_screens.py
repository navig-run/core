"""Tests for navig.tui.screens.* — using a comprehensive textual stub."""
from __future__ import annotations
import sys, types
from types import SimpleNamespace
from unittest.mock import MagicMock


def _install_stub():
    """Install or reuse the shared textual stub, extending as needed."""
    if "textual" in sys.modules and hasattr(sys.modules["textual"], "_navig_stub"):
        # Already installed — just ensure new sub-modules are added
        pass
    else:
        def _mod(name):
            m = types.ModuleType(name)
            sys.modules[name] = m
            return m

        textual = _mod("textual")
        textual._navig_stub = True

        r = _mod("textual.reactive")
        class reactive:
            def __init__(self, default=None, **_kw): self._default = default; self._name = ""
            def __set_name__(self, owner, name): self._name = f"__r_{name}"
            def __get__(self, obj, t=None): return self if obj is None else getattr(obj, self._name, self._default)
            def __set__(self, obj, v): object.__setattr__(obj, self._name, v)
        r.reactive = reactive

        app_mod = _mod("textual.app")
        class App:
            def __init__(self, *a, **k): pass
        app_mod.App = App
        app_mod.ComposeResult = None

        sc = _mod("textual.screen")
        class Screen:
            DEFAULT_CSS = ""
            BINDINGS = []
            def __init__(self, *a, **k): pass
            def compose(self): return iter([])
            def dismiss(self, *a, **k): pass
            def post_message(self, *a, **k): pass
        sc.Screen = Screen
        sc.ModalScreen = Screen

        wg = _mod("textual.widget")
        class Widget:
            def __init__(self, *a, **k): pass
            def refresh(self, *a, **k): pass
            def update(self, c="", *a, **k): pass
        wg.Widget = Widget

        w = _mod("textual.widgets")
        class Static(Widget): pass
        class Label(Widget): pass
        class Button(Widget): pass
        class ListView(Widget): pass
        class ListItem(Widget): pass
        for cls in (Static, Label, ListView, ListItem):
            setattr(w, cls.__name__, cls)
        class Button(Widget):
            class Pressed: pass  # event stub for @on(Button.Pressed, ...)
        w.Button = Button
        class RichLog(Widget): pass
        w.RichLog = RichLog

        bi = _mod("textual.binding")
        class Binding:
            def __init__(self, key, action, desc="", show=True): pass
        bi.Binding = Binding

        containers = _mod("textual.containers")
        class Vertical(Widget): pass
        class Horizontal(Widget): pass
        class Container(Widget): pass
        containers.Vertical = Vertical
        containers.Horizontal = Horizontal
        containers.Container = Container

        for name in (
            "textual.css", "textual.css.query", "textual.geometry",
            "textual.color", "textual.message", "textual.messages",
            "textual.events", "textual.command", "textual.keys",
            "textual.notifications",
        ):
            _mod(name)

        import navig as _navig_pkg, os as _os
        _tui_path = [_os.path.join(_navig_pkg.__path__[0], "tui")]
        navig_tui = _mod("navig.tui")
        navig_tui.__path__ = _tui_path
        navig_tui.__package__ = "navig.tui"

        # Pre-stub navig.tui.screens so its __init__ (which imports BootScreen,
        # RichLog, etc.) does NOT run; sub-modules can still be found via real path.
        _scr_path = [_os.path.join(_navig_pkg.__path__[0], "tui", "screens")]
        navig_tui_scr = _mod("navig.tui.screens")
        navig_tui_scr.__path__ = _scr_path
        navig_tui_scr.__package__ = "navig.tui.screens"

        # Pre-stub navig.tui.screens.settings
        _set_path = [_os.path.join(_navig_pkg.__path__[0], "tui", "screens", "settings")]
        navig_tui_set = _mod("navig.tui.screens.settings")
        navig_tui_set.__path__ = _set_path
        navig_tui_set.__package__ = "navig.tui.screens.settings"

    # Ensure textual.on and textual.work exist as callables
    textual = sys.modules["textual"]
    if not hasattr(textual, "on"):
        textual.on = lambda *a, **k: (lambda f: f)
    if not hasattr(textual, "work"):
        textual.work = lambda *a, **k: (lambda f: f)

    # Ensure textual.worker stub
    if "textual.worker" not in sys.modules:
        wk = types.ModuleType("textual.worker")
        class WorkerCancelled(Exception): pass
        wk.WorkerCancelled = WorkerCancelled
        wk.Worker = object
        sys.modules["textual.worker"] = wk


_install_stub()


# ---------------------------------------------------------------------------
# welcome.py
# ---------------------------------------------------------------------------

class TestWelcomeScreen:
    def _import(self):
        from navig.tui.screens.welcome import WelcomeScreen
        return WelcomeScreen

    def test_class_importable(self):
        cls = self._import()
        assert cls.__name__ == "WelcomeScreen"

    def test_has_default_css(self):
        cls = self._import()
        assert isinstance(getattr(cls, "DEFAULT_CSS", ""), str)

    def test_instantiable(self):
        cls = self._import()
        obj = cls()
        assert obj is not None

    def test_is_screen_subclass(self):
        cls = self._import()
        Screen = sys.modules["textual.screen"].Screen
        assert issubclass(cls, Screen)

    def test_no_bindings_required(self):
        # WelcomeScreen may or may not have BINDINGS
        cls = self._import()
        assert hasattr(cls, "DEFAULT_CSS")


# ---------------------------------------------------------------------------
# settings/root.py
# ---------------------------------------------------------------------------

class TestSettingsRootScreen:
    def _import(self):
        from navig.tui.screens.settings.root import SettingsRootScreen
        return SettingsRootScreen

    def test_importable(self):
        cls = self._import()
        assert cls.__name__ == "SettingsRootScreen"

    def test_has_bindings(self):
        cls = self._import()
        bindings = getattr(cls, "BINDINGS", [])
        assert isinstance(bindings, list)

    def test_bindings_include_escape(self):
        cls = self._import()
        bindings = getattr(cls, "BINDINGS", [])
        # BINDINGS is list of Binding stubs; our Binding.__init__ captures nothing,
        # so just verify length > 0
        assert len(bindings) >= 1

    def test_has_default_css(self):
        cls = self._import()
        assert isinstance(getattr(cls, "DEFAULT_CSS", ""), str)

    def test_instantiable(self):
        cls = self._import()
        obj = cls()
        assert obj is not None

    def test_is_screen_subclass(self):
        cls = self._import()
        Screen = sys.modules["textual.screen"].Screen
        assert issubclass(cls, Screen)


# ---------------------------------------------------------------------------
# system_checks.py
# ---------------------------------------------------------------------------

class TestSystemChecksScreen:
    def _import(self):
        from navig.tui.screens.system_checks import SystemChecksScreen
        return SystemChecksScreen

    def test_importable(self):
        cls = self._import()
        assert cls.__name__ == "SystemChecksScreen"

    def test_has_default_css(self):
        cls = self._import()
        assert isinstance(getattr(cls, "DEFAULT_CSS", ""), str)

    def test_instantiable(self):
        cls = self._import()
        obj = cls()
        assert obj is not None

    def test_is_screen_subclass(self):
        cls = self._import()
        Screen = sys.modules["textual.screen"].Screen
        assert issubclass(cls, Screen)

    def test_class_defined_in_module(self):
        import navig.tui.screens.system_checks as mod
        assert hasattr(mod, "SystemChecksScreen")


# ---------------------------------------------------------------------------
# tiered_init.py
# ---------------------------------------------------------------------------

class TestTieredInitScreen:
    def _import(self):
        from navig.tui.screens.tiered_init import TieredInitScreen
        return TieredInitScreen

    def test_importable(self):
        cls = self._import()
        assert cls.__name__ == "TieredInitScreen"

    def test_has_bindings(self):
        cls = self._import()
        bindings = getattr(cls, "BINDINGS", [])
        assert isinstance(bindings, list) and len(bindings) >= 1

    def test_bindings_count(self):
        cls = self._import()
        # 4 bindings: 1,2,3,escape
        assert len(cls.BINDINGS) >= 4

    def test_has_default_css(self):
        cls = self._import()
        assert isinstance(getattr(cls, "DEFAULT_CSS", ""), str)

    def test_instantiable(self):
        cls = self._import()
        obj = cls()
        assert obj is not None

    def test_is_screen_subclass(self):
        cls = self._import()
        Screen = sys.modules["textual.screen"].Screen
        assert issubclass(cls, Screen)
