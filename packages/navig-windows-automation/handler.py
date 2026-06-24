"""
navig-windows-automation/handler.py

Lifecycle + command registration for the Windows Automation pack.
Uses AHKAdapter from src/ahk_engine.py for all AHK interactions.
Windows-only: commands silently no-op on other platforms.
"""

from __future__ import annotations

import platform
from typing import Any

_IS_WIN = platform.system() == "Windows"
_PACKAGE_DIR = None  # set in on_load from ctx, or lazily from __file__
_adapter_instance = None  # cached AHKAdapter — instantiated once per process


# ── Lifecycle ──────────────────────────────────────────────────────────────────


def on_load(ctx: dict) -> None:
    """Register commands into CommandRegistry on pack activation."""
    global _PACKAGE_DIR
    _PACKAGE_DIR = ctx.get("store_path") or ctx.get("plugin_dir")
    try:
        from navig.commands._registry import CommandRegistry

        CommandRegistry.register("ahk_run", cmd_ahk_run)
        CommandRegistry.register("ahk_type", cmd_ahk_type)
        CommandRegistry.register("ahk_click", cmd_ahk_click)
        CommandRegistry.register("ahk_open_app", cmd_ahk_open_app)
        CommandRegistry.register("ahk_window_list", cmd_ahk_window_list)
        CommandRegistry.register("ahk_window_close", cmd_ahk_window_close)
    except ImportError as exc:
        import logging

        logging.getLogger(__name__).warning(
            "navig-windows-automation: CommandRegistry unavailable — commands not registered: %s",
            exc,
        )


def on_unload(ctx: dict) -> None:
    """Deregister commands on pack deactivation."""
    global _adapter_instance
    _adapter_instance = None
    try:
        from navig.commands._registry import CommandRegistry

        for name in (
            "ahk_run",
            "ahk_type",
            "ahk_click",
            "ahk_open_app",
            "ahk_window_list",
            "ahk_window_close",
        ):
            CommandRegistry.deregister(name)
    except ImportError as exc:
        import logging

        logging.getLogger(__name__).warning(
            "navig-windows-automation: CommandRegistry unavailable — could not deregister: %s",
            exc,
        )


def on_event(event: str, ctx: dict) -> dict | None:
    return None


# ── Helpers ────────────────────────────────────────────────────────────────────


def _get_adapter():
    """Lazy import and cache of AHKAdapter — instantiated once per process."""
    global _adapter_instance
    if _adapter_instance is not None:
        return _adapter_instance
    if not _IS_WIN:
        raise RuntimeError("navig-windows-automation requires Windows")
    import importlib.util
    import pathlib

    # Resolve package dir robustly: prefer ctx-set value, fall back to __file__
    if _PACKAGE_DIR is not None:
        base = pathlib.Path(_PACKAGE_DIR)
    else:
        base = pathlib.Path(__file__).parent
    src = base / "src" / "ahk_engine.py"
    spec = importlib.util.spec_from_file_location("ahk_engine", src)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load ahk_engine from {src}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    _adapter_instance = mod.AHKAdapter()
    return _adapter_instance


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
        if not result.get("success", False):
            return {
                "status": "error",
                "message": result.get("stderr") or "AHK execution failed",
                "data": result,
            }
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
        result = adapter.send_input(text, window_title=window or None)
        if not result.success:
            return {
                "status": "error",
                "message": result.stderr or "AHK input failed",
            }
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
        result = adapter.click(int(x), int(y), button=button)
        if not result.success:
            return {
                "status": "error",
                "message": result.stderr or "AHK click failed",
            }
        return {"status": "ok", "data": {"x": x, "y": y, "button": button}}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def cmd_ahk_open_app(args: dict, ctx: Any = None) -> dict:
    """
    Open an application or file via AutoHotkey.

    args:
      target (str): App name, executable path, or file path.
    """
    target = args.get("target", "")
    if not target:
        return {"status": "error", "message": "Missing 'target' argument"}
    if not _IS_WIN:
        return {"status": "error", "message": "Windows only"}
    try:
        adapter = _get_adapter()
        result = adapter.open_app(target)
        if not result.success:
            return {
                "status": "error",
                "message": result.stderr or "AHK open app failed",
            }
        return {"status": "ok", "data": {"target": target, "output": result.stdout}}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def cmd_ahk_window_list(args: dict, ctx: Any = None) -> dict:
    """Return all visible windows."""
    if not _IS_WIN:
        return {"status": "error", "message": "Windows only"}
    try:
        adapter = _get_adapter()
        windows = adapter.get_all_windows()
        return {"status": "ok", "data": {"windows": [w.to_dict() for w in windows]}}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def cmd_ahk_window_close(args: dict, ctx: Any = None) -> dict:
    """
    Close a window by selector/title.

    args:
      title (str): Window selector or title.
    """
    title = args.get("title", "")
    if not title:
        return {"status": "error", "message": "Missing 'title' argument"}
    if not _IS_WIN:
        return {"status": "error", "message": "Windows only"}
    try:
        adapter = _get_adapter()
        result = adapter.close_window(title)
        if not result.success:
            return {
                "status": "error",
                "message": result.stderr or "AHK close window failed",
            }
        return {"status": "ok", "data": {"title": title}}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ── COMMANDS registry (used by authoring tools) ───────────────────────────────

COMMANDS: dict[str, Any] = {
    "ahk_run": cmd_ahk_run,
    "ahk_type": cmd_ahk_type,
    "ahk_click": cmd_ahk_click,
    "ahk_open_app": cmd_ahk_open_app,
    "ahk_window_list": cmd_ahk_window_list,
    "ahk_window_close": cmd_ahk_window_close,
}
