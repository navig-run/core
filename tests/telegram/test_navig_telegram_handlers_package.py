from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packages" / "navig-telegram-handlers"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_handler_scopes_telegram_path_for_load_and_unload(monkeypatch):
    for module_name in ("formatters", "menus", "navig_telegram"):
        sys.modules.pop(module_name, None)

    registered_formatters: list[str] = []
    registered_menus: list[str] = []
    deregistered_formatters: list[str] = []
    deregistered_menus: list[str] = []

    handler_registry = types.SimpleNamespace(
        register_formatter=lambda name, fn: registered_formatters.append(name),
        register_menu=lambda name, fn: registered_menus.append(name),
        deregister_formatter=lambda name: deregistered_formatters.append(name),
        deregister_menu=lambda name: deregistered_menus.append(name),
    )
    navig_telegram = types.ModuleType("navig_telegram")
    navig_telegram.handler_registry = handler_registry
    monkeypatch.setitem(sys.modules, "navig_telegram", navig_telegram)

    handler = _load_module(
        "navig_telegram_handlers_pack", PACK_ROOT / "handler.py"
    )
    ctx = handler.PluginContext(
        pack_id="navig-telegram-handlers",
        version="1.0.0",
        store_path=PACK_ROOT,
        config={},
    )
    telegram_path = str(PACK_ROOT / "telegram")

    assert telegram_path not in sys.path

    handler.on_load(ctx)
    assert telegram_path not in sys.path
    assert registered_formatters == ["checkdomain"]
    assert registered_menus == ["checkdomain"]

    handler.on_unload(ctx)
    assert telegram_path not in sys.path
    assert deregistered_formatters == ["checkdomain"]
    assert deregistered_menus == ["checkdomain"]
