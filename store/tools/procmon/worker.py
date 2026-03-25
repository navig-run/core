"""
procmon worker — silent Process Monitor capture.
CLI path: sys procmon capture | stop
"""
from __future__ import annotations
import json, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1] / "_lib"))
from common import ok, err, emit, run, Timer, current_os, resolve_usb_exe

TOOL = "procmon"

def _exe(): return resolve_usb_exe("procmon")
def _check(): return "procmon is Windows-only." if current_os() != "windows" else None

def cmd_capture(args: dict) -> dict:
    t = Timer()
    if e := _check(): return err(TOOL, "capture", [e], ms=t.ms())
    try:
        log_file = args["log_file"]
        duration = args.get("duration", 30)
        dry = args.get("dry_run", False)
        exe = _exe()
        # Start silent capture
        cmd = [exe, "/Quiet", "/Minimized", "/BackingFile", log_file]
        if dry:
            return ok(TOOL, "capture",
                      {"dry_run": True, "cmd": [str(c) for c in cmd],
                       "log_file": log_file, "duration": duration}, ms=t.ms())
        import subprocess, time
        proc = subprocess.Popen([str(c) for c in cmd])
        if duration > 0:
            time.sleep(duration)
            # Terminate capture
            subprocess.run([str(exe), "/Terminate"], capture_output=True)
            proc.wait(timeout=10)
        return ok(TOOL, "capture", {"pid": proc.pid, "log_file": log_file,
                                     "duration": duration,
                                     "stopped": duration > 0}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "capture", [str(e)], ms=t.ms())

def cmd_stop(args: dict) -> dict:
    t = Timer()
    if e := _check(): return err(TOOL, "stop", [e], ms=t.ms())
    try:
        exe = _exe()
        rc, out, er = run([exe, "/Terminate"], dry_run=args.get("dry_run", False))
        return ok(TOOL, "stop", {"output": out, "rc": rc}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "stop", [str(e)], ms=t.ms())

HANDLERS = {"capture": cmd_capture, "stop": cmd_stop}

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
