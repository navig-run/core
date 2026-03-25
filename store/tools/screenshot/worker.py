"""
screenshot worker — cross-platform screen capture.
Windows: PIL/mss, macOS: screencapture, Linux: scrot/gnome-screenshot.
CLI path: sys screenshot take | monitors
"""
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import ok, err, emit, run, Timer, current_os, find_on_path

TOOL = "screenshot"

def _default_output() -> str:
    import tempfile, time
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
    cmd = ["screencapture", "-x"]
    if monitor > 0:
        cmd.extend(["-D", str(monitor)])
    cmd.append(output)
    import subprocess
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode == 0, r.stderr.decode().strip()

def _take_linux(output: str) -> tuple[bool, str]:
    for tool in ["scrot", "gnome-screenshot", "import"]:
        p = find_on_path(tool)
        if p:
            if tool == "scrot":
                import subprocess
                r = subprocess.run([str(p), output], capture_output=True)
            elif tool == "gnome-screenshot":
                import subprocess
                r = subprocess.run([str(p), "-f", output], capture_output=True)
            else:
                import subprocess
                r = subprocess.run([str(p), "-window", "root", output], capture_output=True)
            return r.returncode == 0, r.stderr.decode().strip()
    return False, "No screenshot tool found. Install scrot: sudo apt install scrot"

def cmd_take(args: dict) -> dict:
    t = Timer()
    output  = args.get("output") or _default_output()
    monitor = args.get("monitor", 0)
    dry     = args.get("dry_run", False)
    if dry:
        return ok(TOOL, "take", {"dry_run": True, "output": output, "monitor": monitor}, ms=t.ms())
    try:
        os_key = current_os()
        if os_key == "windows":
            success, msg = _take_windows(output, monitor)
        elif os_key == "mac":
            success, msg = _take_mac(output, monitor)
        else:
            success, msg = _take_linux(output)
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
                        monitors.append({"index": i, "left": m["left"], "top": m["top"],
                                         "width": m["width"], "height": m["height"]})
            except ImportError:
                monitors.append({"note": "Install mss for monitor enumeration: pip install mss"})
        elif os_key == "mac":
            rc, out, _ = run(["system_profiler", "SPDisplaysDataType"])
            monitors.append({"raw": out[:500]})
        else:
            p = find_on_path("xrandr")
            if p:
                rc, out, _ = run([p, "--query"])
                for line in out.splitlines():
                    if " connected" in line:
                        monitors.append({"raw": line.strip()})
        return ok(TOOL, "monitors", {"monitors": monitors, "count": len(monitors)}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "monitors", [str(e)], ms=t.ms())

HANDLERS = {"take": cmd_take, "monitors": cmd_monitors}

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        try:
            req = json.loads(line)
            handler = HANDLERS.get(req.get("command",""))
            emit(handler(req.get("args",{})) if handler
                 else err(TOOL, req.get("command","?"), ["Unknown command"]))
        except Exception as e:
            emit(err(TOOL, "?", [str(e)]))

if __name__ == "__main__":
    main()
