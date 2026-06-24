from __future__ import annotations

from pathlib import Path

from navig.core.kernel import NavigKernel
from navig.core.models import NavigCommand


def _make_windows_command(name: str) -> NavigCommand:
    return NavigCommand(
        name=name,
        syntax=f"navig windows {name}",
        description=f"windows {name}",
        source_skill="windows-automation",
    )


def test_kernel_maps_windows_skill_commands_to_ahk_registry(monkeypatch):
    kernel = NavigKernel(str(Path.cwd()))
    kernel.commands = {
        "open-app": _make_windows_command("open-app"),
        "window-list": _make_windows_command("window-list"),
        "window-close": _make_windows_command("window-close"),
        "type": _make_windows_command("type"),
        "click": _make_windows_command("click"),
    }
    dispatched: list[tuple[str, dict]] = []
    monkeypatch.setattr(kernel, "_dispatch_registry", lambda method, params: dispatched.append((method, params)))

    kernel.execute_command("open-app", ["notepad"])
    kernel.execute_command("window-list", [])
    kernel.execute_command("window-close", ["Untitled", "-", "Notepad"])
    kernel.execute_command("type", ["Hello", "World"])
    kernel.execute_command("click", ["10", "20", "right", "2"])

    assert dispatched == [
        ("ahk_open_app", {"target": "notepad"}),
        ("ahk_window_list", {}),
        ("ahk_window_close", {"title": "Untitled - Notepad"}),
        ("ahk_type", {"text": "Hello World"}),
        ("ahk_click", {"x": "10", "y": "20", "button": "right", "clicks": "2"}),
    ]
