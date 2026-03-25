"""
vivetool worker — enable/disable/query Windows feature flags.
CLI path: sys vivetool enable | disable | query
"""
from __future__ import annotations
import json, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1] / "_lib"))
from common import ok, err, emit, run, Timer, current_os, resolve_usb_exe

TOOL = "vivetool"

def _exe(): return resolve_usb_exe("vivetool")
def _check(): return "vivetool is Windows-only." if current_os() != "windows" else None

def cmd_enable(args: dict) -> dict:
    t = Timer()
    if e := _check(): return err(TOOL, "enable", [e], ms=t.ms())
    try:
        rc, out, er = run([_exe(), "/enable", f"/id:{args['id']}"],
                          dry_run=args.get("dry_run", False))
        if rc != 0 and not args.get("dry_run"):
            return err(TOOL, "enable", [er or out], ms=t.ms())
        return ok(TOOL, "enable", {"feature_id": args["id"], "output": out}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "enable", [str(e)], ms=t.ms())

def cmd_disable(args: dict) -> dict:
    t = Timer()
    if e := _check(): return err(TOOL, "disable", [e], ms=t.ms())
    try:
        rc, out, er = run([_exe(), "/disable", f"/id:{args['id']}"],
                          dry_run=args.get("dry_run", False))
        if rc != 0 and not args.get("dry_run"):
            return err(TOOL, "disable", [er or out], ms=t.ms())
        return ok(TOOL, "disable", {"feature_id": args["id"], "output": out}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "disable", [str(e)], ms=t.ms())

def cmd_query(args: dict) -> dict:
    t = Timer()
    if e := _check(): return err(TOOL, "query", [e], ms=t.ms())
    try:
        rc, out, er = run([_exe(), "/query", f"/id:{args['id']}"])
        return ok(TOOL, "query", {"feature_id": args["id"], "output": out, "rc": rc}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "query", [str(e)], ms=t.ms())

HANDLERS = {"enable": cmd_enable, "disable": cmd_disable, "query": cmd_query}

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
