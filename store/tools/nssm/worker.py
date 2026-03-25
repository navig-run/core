"""
nssm worker — Windows service management via NSSM.
CLI path: sys nssm install | start | stop | remove | status
"""
from __future__ import annotations
import json, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1] / "_lib"))
from common import ok, err, emit, run, Timer, current_os, resolve_usb_exe, is_admin

TOOL = "nssm"

def _exe():
    return resolve_usb_exe("nssm")

def _check_os() -> str | None:
    if current_os() != "windows":
        return "nssm is Windows-only."
    return None

def cmd_install(args: dict) -> dict:
    t = Timer()
    if e := _check_os(): return err(TOOL, "install", [e], ms=t.ms())
    try:
        exe = _exe()
        svc = args["service_name"]
        prog = args["program"]
        dry = args.get("dry_run", False)
        if not dry and not is_admin():
            return err(TOOL, "install", ["Admin required to install services."], ms=t.ms())
        rc, out, er = run([exe, "install", svc, prog], dry_run=dry)
        if rc not in (0,) and not dry:
            return err(TOOL, "install", [er or out], ms=t.ms())
        return ok(TOOL, "install", {"service": svc, "program": prog,
                                     "installed": not dry, "dry_run": dry}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "install", [str(e)], ms=t.ms())

def cmd_start(args: dict) -> dict:
    t = Timer()
    if e := _check_os(): return err(TOOL, "start", [e], ms=t.ms())
    try:
        rc, out, er = run([_exe(), "start", args["service_name"]],
                          dry_run=args.get("dry_run", False))
        if rc not in (0,) and not args.get("dry_run"):
            return err(TOOL, "start", [er or out], ms=t.ms())
        return ok(TOOL, "start", {"service": args["service_name"], "output": out}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "start", [str(e)], ms=t.ms())

def cmd_stop(args: dict) -> dict:
    t = Timer()
    if e := _check_os(): return err(TOOL, "stop", [e], ms=t.ms())
    try:
        rc, out, er = run([_exe(), "stop", args["service_name"]],
                          dry_run=args.get("dry_run", False))
        if rc not in (0,) and not args.get("dry_run"):
            return err(TOOL, "stop", [er or out], ms=t.ms())
        return ok(TOOL, "stop", {"service": args["service_name"], "output": out}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "stop", [str(e)], ms=t.ms())

def cmd_remove(args: dict) -> dict:
    t = Timer()
    if e := _check_os(): return err(TOOL, "remove", [e], ms=t.ms())
    try:
        if not args.get("yes") and not args.get("dry_run"):
            return err(TOOL, "remove", ["Pass --yes to confirm service removal."], ms=t.ms())
        rc, out, er = run([_exe(), "remove", args["service_name"], "confirm"],
                          dry_run=args.get("dry_run", False))
        if rc not in (0,) and not args.get("dry_run"):
            return err(TOOL, "remove", [er or out], ms=t.ms())
        return ok(TOOL, "remove", {"service": args["service_name"], "removed": True}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "remove", [str(e)], ms=t.ms())

def cmd_status(args: dict) -> dict:
    t = Timer()
    if e := _check_os(): return err(TOOL, "status", [e], ms=t.ms())
    try:
        rc, out, er = run([_exe(), "status", args["service_name"]])
        return ok(TOOL, "status", {"service": args["service_name"],
                                    "status": out or er, "rc": rc}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "status", [str(e)], ms=t.ms())

HANDLERS = {"install": cmd_install, "start": cmd_start, "stop": cmd_stop,
            "remove": cmd_remove, "status": cmd_status}

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
