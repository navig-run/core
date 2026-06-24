from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

from navig.adapters.automation.types import ExecutionResult

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SRC = ROOT / "packages" / "navig-windows-automation" / "src"
HANDLER_PATH = ROOT / "packages" / "navig-windows-automation" / "handler.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_package_ahk_adapter_run_script_executes_inline():
    module = _load_module("package_ahk_engine_inline", PACKAGE_SRC / "ahk_engine.py")
    adapter = module.AHKAdapter.__new__(module.AHKAdapter)

    captured: dict[str, object] = {}

    def fake_execute(script: str, timeout: float | None = None):
        captured["script"] = script
        captured["timeout"] = timeout
        return ExecutionResult(success=True, stdout="ok")

    adapter.execute = fake_execute

    result = adapter.run_script('MsgBox "Hello"', timeout=5)

    assert result["success"] is True
    assert result["stdout"] == "ok"
    assert captured == {"script": 'MsgBox "Hello"', "timeout": 5}


def test_package_ahk_adapter_run_script_executes_file(tmp_path: Path):
    module = _load_module("package_ahk_engine_file", PACKAGE_SRC / "ahk_engine.py")
    adapter = module.AHKAdapter.__new__(module.AHKAdapter)
    script_path = tmp_path / "sample.ahk"
    script_path.write_text('MsgBox "Hello"', encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_execute_file(path: Path, timeout: float | None = None):
        captured["path"] = path
        captured["timeout"] = timeout
        return ExecutionResult(success=True, stdout="file-ok")

    adapter.execute_file = fake_execute_file

    result = adapter.run_script(str(script_path))

    assert result["success"] is True
    assert result["stdout"] == "file-ok"
    assert captured == {"path": script_path, "timeout": None}


def test_package_ahk_adapter_send_input_activates_target_window():
    module = _load_module("package_ahk_engine_send_input", PACKAGE_SRC / "ahk_engine.py")
    adapter = module.AHKAdapter.__new__(module.AHKAdapter)
    calls: list[tuple[str, str]] = []

    def fake_activate_window(window_title: str):
        calls.append(("activate", window_title))
        return ExecutionResult(success=True)

    def fake_type_text(text: str):
        calls.append(("type", text))
        return ExecutionResult(success=True, stdout="typed")

    adapter.activate_window = fake_activate_window
    adapter.type_text = fake_type_text

    result = adapter.send_input("hello", window_title="Notepad")

    assert result.success is True
    assert calls == [("activate", "Notepad"), ("type", "hello")]


def test_handler_returns_error_when_ahk_run_fails(monkeypatch):
    handler = _load_module("package_ahk_handler_run", HANDLER_PATH)
    monkeypatch.setattr(handler, "_IS_WIN", True)
    monkeypatch.setattr(
        handler,
        "_get_adapter",
        lambda: type(
            "FakeAdapter",
            (),
            {"run_script": lambda self, script: {"success": False, "stderr": "boom"}},
        )(),
    )

    result = handler.cmd_ahk_run({"script": 'MsgBox "Hello"'})

    assert result["status"] == "error"
    assert result["message"] == "boom"


def test_handler_returns_error_when_send_input_fails(monkeypatch):
    handler = _load_module("package_ahk_handler_type", HANDLER_PATH)
    monkeypatch.setattr(handler, "_IS_WIN", True)
    monkeypatch.setattr(
        handler,
        "_get_adapter",
        lambda: type(
            "FakeAdapter",
            (),
            {
                "send_input": lambda self, text, window_title=None: ExecutionResult(
                    success=False, stderr="no target"
                )
            },
        )(),
    )

    result = handler.cmd_ahk_type({"text": "hello", "window": "Missing"})

    assert result == {"status": "error", "message": "no target"}


def test_handler_registers_full_ahk_command_set(monkeypatch):
    handler = _load_module("package_ahk_handler_register", HANDLER_PATH)
    registered: list[str] = []

    registry = types.SimpleNamespace(
        register=lambda name, handler, pack_id=None: registered.append(name)
    )
    monkeypatch.setitem(
        sys.modules,
        "navig.commands._registry",
        types.SimpleNamespace(CommandRegistry=registry),
    )

    handler.on_load({"store_path": ROOT / "packages" / "navig-windows-automation"})

    assert registered == [
        "ahk_run",
        "ahk_type",
        "ahk_click",
        "ahk_open_app",
        "ahk_window_list",
        "ahk_window_close",
    ]


def test_handler_window_commands_use_adapter(monkeypatch):
    handler = _load_module("package_ahk_handler_windows", HANDLER_PATH)
    monkeypatch.setattr(handler, "_IS_WIN", True)

    class FakeWindow:
        def __init__(self, title: str):
            self.title = title

        def to_dict(self):
            return {"title": self.title}

    monkeypatch.setattr(
        handler,
        "_get_adapter",
        lambda: type(
            "FakeAdapter",
            (),
            {
                "open_app": lambda self, target: ExecutionResult(
                    success=True, stdout=f"opened:{target}"
                ),
                "get_all_windows": lambda self: [FakeWindow("Notepad")],
                "close_window": lambda self, title: ExecutionResult(success=True),
            },
        )(),
    )

    open_result = handler.cmd_ahk_open_app({"target": "notepad"})
    list_result = handler.cmd_ahk_window_list({})
    close_result = handler.cmd_ahk_window_close({"title": "Notepad"})

    assert open_result == {
        "status": "ok",
        "data": {"target": "notepad", "output": "opened:notepad"},
    }
    assert list_result == {"status": "ok", "data": {"windows": [{"title": "Notepad"}]}}
    assert close_result == {"status": "ok", "data": {"title": "Notepad"}}
