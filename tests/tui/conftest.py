"""Conftest for tests/tui/ — installs a complete textual stub so all TUI tests
can run without textual being installed.  Runs before any test file in this
subtree is collected, so navig/tui/__init__.py never executes a real
textual import.

This is a superset of the stubs used by test_tui_widgets.py and
test_tui_screens.py so those files' own _install_stub() calls detect
_navig_stub and skip re-installation cleanly.
"""
from __future__ import annotations

import sys
import types


def _install_textual_stub():  # noqa: C901
    if "textual" in sys.modules and hasattr(sys.modules["textual"], "_navig_stub"):
        # Already installed — ensure on/work exist then return
        tx = sys.modules["textual"]
        if not hasattr(tx, "on"):
            tx.on = lambda *a, **k: (lambda f: f)
        if not hasattr(tx, "work"):
            tx.work = lambda *a, **k: (lambda f: f)
        return

    def _mod(name):
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    textual = _mod("textual")
    textual._navig_stub = True
    textual.on = lambda *a, **k: (lambda f: f)
    textual.work = lambda *a, **k: (lambda f: f)

    r = _mod("textual.reactive")

    class reactive:
        def __init__(self, default=None, **_kw):
            self._default = default
            self._name = ""

        def __set_name__(self, owner, name):
            self._name = f"__r_{name}"

        def __get__(self, obj, t=None):
            return self if obj is None else getattr(obj, self._name, self._default)

        def __set__(self, obj, v):
            object.__setattr__(obj, self._name, v)

    r.reactive = reactive

    app_mod = _mod("textual.app")

    class App:
        def __init__(self, *a, **k):
            pass
        def push_screen(self, *a, **k):
            pass

    app_mod.App = App
    app_mod.ComposeResult = None

    sc = _mod("textual.screen")

    class Screen:
        DEFAULT_CSS = ""
        BINDINGS = []

        def __init__(self, *a, **k):
            pass
        def compose(self):
            return iter([])
        def dismiss(self, *a, **k):
            pass
        def post_message(self, *a, **k):
            pass

    sc.Screen = Screen
    sc.ModalScreen = Screen

    wg = _mod("textual.widget")

    class Widget:
        def __init__(self, *a, **k):
            pass
        def refresh(self, *a, **k):
            pass
        def update(self, c="", *a, **k):
            pass
        def compose(self):
            return iter([])

    wg.Widget = Widget

    w = _mod("textual.widgets")

    class Static(Widget):
        pass

    class Label(Widget):
        pass

    class Button(Widget):
        class Pressed:
            pass

    class ListView(Widget):
        pass

    class ListItem(Widget):
        pass

    class RichLog(Widget):
        pass

    for cls in (Static, Label, Button, ListView, ListItem, RichLog):
        setattr(w, cls.__name__, cls)

    _b = _mod("textual.binding")

    class Binding:
        def __init__(self, key, action, description="", show=True, **kw):
            pass

    _b.Binding = Binding
    _b.BindingType = Binding

    containers = _mod("textual.containers")

    class Vertical(Widget):
        pass

    class Horizontal(Widget):
        pass

    class Container(Widget):
        pass

    class ScrollableContainer(Widget):
        pass

    containers.Vertical = Vertical
    containers.Horizontal = Horizontal
    containers.Container = Container
    containers.ScrollableContainer = ScrollableContainer

    wk = _mod("textual.worker")

    class WorkerCancelled(Exception):
        pass

    wk.WorkerCancelled = WorkerCancelled
    wk.Worker = object

    for name in (
        "textual.css",
        "textual.css.query",
        "textual.geometry",
        "textual.color",
        "textual.message",
        "textual.messages",
        "textual.events",
        "textual.command",
        "textual.keys",
        "textual.notifications",
        "textual.work",
    ):
        _mod(name)

    import navig as _navig_pkg
    import os as _os

    # Pre-stub navig.tui — prevents __init__.py execution; submodules loadable
    # via real __path__.
    _tui_path = [_os.path.join(_navig_pkg.__path__[0], "tui")]
    navig_tui = _mod("navig.tui")
    navig_tui.__path__ = _tui_path
    navig_tui.__package__ = "navig.tui"

    _widgets_path = [_os.path.join(_navig_pkg.__path__[0], "tui", "widgets")]
    navig_tui_widgets = _mod("navig.tui.widgets")
    navig_tui_widgets.__path__ = _widgets_path
    navig_tui_widgets.__package__ = "navig.tui.widgets"

    _scr_path = [_os.path.join(_navig_pkg.__path__[0], "tui", "screens")]
    navig_tui_scr = _mod("navig.tui.screens")
    navig_tui_scr.__path__ = _scr_path
    navig_tui_scr.__package__ = "navig.tui.screens"

    _set_path = [_os.path.join(_navig_pkg.__path__[0], "tui", "screens", "settings")]
    navig_tui_set = _mod("navig.tui.screens.settings")
    navig_tui_set.__path__ = _set_path
    navig_tui_set.__package__ = "navig.tui.screens.settings"


_install_textual_stub()
