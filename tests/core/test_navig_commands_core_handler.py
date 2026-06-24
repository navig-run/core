from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HANDLER_PATH = ROOT / "packages" / "navig-commands-core" / "handler.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_on_event_handles_sync_command_handler(monkeypatch):
    module = _load_module("navig_commands_core_handler_sync", HANDLER_PATH)
    commands_module = types.ModuleType("commands")
    commands_module.COMMANDS = {
        "ping": lambda payload, ctx: {"status": "ok", "payload": payload, "pack": ctx.pack_id}
    }
    monkeypatch.setitem(sys.modules, "commands", commands_module)

    ctx = module.PluginContext(
        pack_id="navig-commands-core",
        version="1.0.0",
        store_path=ROOT / "packages" / "navig-commands-core",
        config={},
    )
    event = module.PluginEvent(name="ping", payload={"host": "example.com"}, source="test")

    result = module.on_event(event, ctx)

    assert result == {
        "status": "ok",
        "payload": {"host": "example.com"},
        "pack": "navig-commands-core",
    }


def test_on_event_handles_async_command_handler(monkeypatch):
    module = _load_module("navig_commands_core_handler_async", HANDLER_PATH)
    commands_module = types.ModuleType("commands")

    async def _handle(payload, ctx):
        return {"status": "ok", "source": payload["source"], "pack": ctx.pack_id}

    commands_module.COMMANDS = {"checkdomain": _handle}
    monkeypatch.setitem(sys.modules, "commands", commands_module)

    ctx = module.PluginContext(
        pack_id="navig-commands-core",
        version="1.0.0",
        store_path=ROOT / "packages" / "navig-commands-core",
        config={},
    )
    event = module.PluginEvent(
        name="checkdomain", payload={"source": "event"}, source="test"
    )

    result = module.on_event(event, ctx)

    assert result == {
        "status": "ok",
        "source": "event",
        "pack": "navig-commands-core",
    }
