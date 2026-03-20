"""
navig-windows-automation/handler.py

Lifecycle + command registration for the Windows Automation pack.
Uses AHKAdapter from src/ahk_engine.py for all AHK interactions.
Windows-only: commands silently no-op on other platforms.
"""
from __future__ import annotations

import platform
import sys
from typing import Any

_IS_WIN = platform.system() == "Windows"


# ── Lifecycle ──────────────────────────────────────────────────────────────────

def on_load(ctx: dict) -> None:
    """Register commands into CommandRegistry on pack activation."""
    try:
        from navig.commands._registry import CommandRegistry
        CommandRegistry.register("ahk_run", cmd_ahk_run)
        CommandRegistry.register("ahk_type", cmd_ahk_type)
        CommandRegistry.register("ahk_click", cmd_ahk_click)
    except ImportError:
        pass  # standalone / authoring mode


def on_unload(ctx: dict) -> None:
    """Deregister commands on pack deactivation."""
    try:
        from navig.commands._registry import CommandRegistry
        for name in ("ahk_run", "ahk_type", "ahk_click"):
            CommandRegistry.deregister(name)
    except ImportError:
        pass  # optional dependency not installed; feature disabled


def on_event(event: str, ctx: dict) -> dict | None:
    return None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_adapter():
    """Lazy import of AHKAdapter — deferred so startup is instant."""
    if not _IS_WIN:
        raise RuntimeError("navig-windows-automation requires Windows")
    plugin_dir = sys.modules[__name__].__file__
    import pathlib
    src = pathlib.Path(plugin_dir).parent / "src" / "ahk_engine.py"
    import importlib.util
    spec = importlib.util.spec_from_file_location("ahk_engine", src)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load ahk_engine from {src}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.AHKAdapter()


# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_ahk_run(args: dict, ctx: Any = None) -> dict:
    """
    Run an AHK script or send keystrokes.

    args:
      script (str): AHK script content or file path.
    """
    script = args.get("script", "")
    if not script:
        return {"status": "error", "message": "Missing 'script' argument"}
    if not _IS_WIN:
        return {"status": "error", "message": "Windows only"}
    try:
        adapter = _get_adapter()
        result = adapter.run_script(script)
        return {"status": "ok", "data": result}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def cmd_ahk_type(args: dict, ctx: Any = None) -> dict:
    """
    Type text into the active window via AHK SendInput.

    args:
      text (str): Text to type.
      window (str, optional): Window title to activate first.
    """
    text = args.get("text", "")
    window = args.get("window", "")
    if not text:
        return {"status": "error", "message": "Missing 'text' argument"}
    if not _IS_WIN:
        return {"status": "error", "message": "Windows only"}
    try:
        adapter = _get_adapter()
        adapter.send_input(text, window_title=window or None)
        return {"status": "ok", "data": {"typed": len(text)}}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def cmd_ahk_click(args: dict, ctx: Any = None) -> dict:
    """
    Click at screen coordinates or on a window element via AHK.

    args:
      x (int): X coordinate.
      y (int): Y coordinate.
      button (str, optional): 'left' (default), 'right', 'middle'.
    """
    x = args.get("x")
    y = args.get("y")
    button = args.get("button", "left")
    if x is None or y is None:
        return {"status": "error", "message": "Missing 'x' and/or 'y' arguments"}
    if not _IS_WIN:
        return {"status": "error", "message": "Windows only"}
    try:
        adapter = _get_adapter()
        adapter.click(int(x), int(y), button=button)
        return {"status": "ok", "data": {"x": x, "y": y, "button": button}}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ── COMMANDS registry (used by authoring tools) ───────────────────────────────

COMMANDS: dict[str, Any] = {
    "ahk_run": cmd_ahk_run,
    "ahk_type": cmd_ahk_type,
    "ahk_click": cmd_ahk_click,
}
