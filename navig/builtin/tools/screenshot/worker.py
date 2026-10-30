"""
screenshot worker — cross-platform screen capture.
Windows: PIL/mss in-process. macOS: screencapture. Linux: the tool that this SESSION has
(grim/gnome-screenshot/spectacle on Wayland, scrot/import/… on X11) — see
``navig.platform.capture`` for why the choice is made there and not here.
CLI path: sys screenshot take | monitors
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import Timer, current_os, emit, err, find_on_path, ok, run

from navig.platform import capture

TOOL = "screenshot"


def _default_output() -> str:
    import tempfile
    import time

    ts = int(time.time())
    return str(Path(tempfile.gettempdir()) / f"screenshot_{ts}.png")


def _take_windows(output: str, monitor: int) -> tuple[bool, str]:
    try:
        from PIL import ImageGrab

        img = ImageGrab.grab(all_screens=(monitor == 0))
        img.save(output)
        return True, ""
    except ImportError:
        pass  # optional dependency not installed; feature disabled
    # Fallback: mss
    try:
        import mss

        with mss.mss() as sct:
            idx = monitor if monitor > 0 else 1
            monitors = sct.monitors
            if idx >= len(monitors):
                idx = 0
            shot = sct.grab(monitors[idx])
            from mss.tools import to_png

            to_png(shot.rgb, shot.size, output=output)
            return True, ""
    except ImportError:
        return False, "Install 'pillow' or 'mss': pip install mss"


def _take_mac(output: str, monitor: int) -> tuple[bool, str]:
    backend = capture.choose("macos")
    if backend is None:  # unreachable on a real mac; keeps the message honest
        return False, capture.install_hint("macos")
    return _take_argv(backend, output, monitor)


def _take_argv(backend: capture.CaptureBackend, output: str, monitor: int) -> tuple[bool, str]:
    """Run one seam backend and report the OS tool's own words when it fails."""
    import subprocess

    argv = backend.argv(output, monitor=monitor)
    if argv is None:
        return False, f"{backend.name} is not a command-line capture backend"
    r = subprocess.run(argv, capture_output=True)
    if r.returncode == 0:
        return True, ""
    detail = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()
    return False, detail or f"{backend.name} exited {r.returncode}"


def _take_linux(output: str, monitor: int) -> tuple[bool, str]:
    """Capture with a backend that exists in THIS Linux session (Wayland or X11).

    The previous list (scrot → gnome-screenshot → import) was X11-only, so a Wayland
    desktop failed every time and the advice named `scrot`, which cannot capture
    Wayland even when installed. The session decides now, in one place.
    """
    current = capture.session()
    backend = capture.choose(current)
    if backend is None:
        return False, capture.install_hint(current)
    return _take_argv(backend, output, monitor)


def cmd_take(args: dict) -> dict:
    t = Timer()
    output = args.get("output") or _default_output()
    monitor = args.get("monitor", 0)
    dry = args.get("dry_run", False)
    if dry:
        return ok(
            TOOL,
            "take",
            {"dry_run": True, "output": output, "monitor": monitor},
            ms=t.ms(),
        )
    try:
        os_key = current_os()
        if os_key == "windows":
            success, msg = _take_windows(output, monitor)
        elif os_key == "mac":
            success, msg = _take_mac(output, monitor)
        else:
            success, msg = _take_linux(output, monitor)
        if not success:
            return err(TOOL, "take", [msg or "Screenshot failed"], ms=t.ms())
        size = Path(output).stat().st_size if Path(output).exists() else 0
        return ok(TOOL, "take", {"output": output, "size_bytes": size}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "take", [str(e)], ms=t.ms())


def cmd_monitors(args: dict) -> dict:
    t = Timer()
    try:
        monitors = []
        os_key = current_os()
        if os_key == "windows":
            try:
                import mss

                with mss.mss() as sct:
                    for i, m in enumerate(sct.monitors):
                        monitors.append(
                            {
                                "index": i,
                                "left": m["left"],
                                "top": m["top"],
                                "width": m["width"],
                                "height": m["height"],
                            }
                        )
            except ImportError:
                monitors.append(
                    {"note": "Install mss for monitor enumeration: pip install mss"}
                )
        elif os_key == "mac":
            rc, out, _ = run(["system_profiler", "SPDisplaysDataType"])
            monitors.append({"raw": out[:500]})
        elif capture.session() == "wayland":
            # xrandr reads the X11 root window; Wayland compositors do not have one that
            # describes their outputs. wlr-randr answers on wlroots compositors, and the
            # honest answer elsewhere is the session, not an empty list.
            p = find_on_path("wlr-randr")
            if p:
                rc, out, _ = run([p])
                for line in out.splitlines():
                    if line and not line.startswith(" "):
                        monitors.append({"raw": line.strip()})
            else:
                monitors.append(
                    {
                        "note": "monitor enumeration needs wlr-randr on Wayland",
                        "session": "wayland",
                    }
                )
        else:
            p = find_on_path("xrandr")
            if p:
                rc, out, _ = run([p, "--query"])
                for line in out.splitlines():
                    if " connected" in line:
                        monitors.append({"raw": line.strip()})
        return ok(
            TOOL, "monitors", {"monitors": monitors, "count": len(monitors)}, ms=t.ms()
        )
    except Exception as e:
        return err(TOOL, "monitors", [str(e)], ms=t.ms())


HANDLERS = {"take": cmd_take, "monitors": cmd_monitors}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            handler = HANDLERS.get(req.get("command", ""))
            emit(
                handler(req.get("args", {}))
                if handler
                else err(TOOL, req.get("command", "?"), ["Unknown command"])
            )
        except Exception as e:
            emit(err(TOOL, "?", [str(e)]))


if __name__ == "__main__":
    main()
