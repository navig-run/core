"""
futurerestore worker — restore iOS device using saved .shsh2 blob.
CLI path: ios futurerestore restore
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1] / "_lib"))
from pathlib import Path

from common import Timer, current_os, emit, err, ok, resolve_usb_exe, run

TOOL = "futurerestore"


def _check():
    return (
        "futurerestore is Windows-only in this config."
        if current_os() != "windows"
        else None
    )


def cmd_restore(args: dict) -> dict:
    t = Timer()
    if e := _check():
        return err(TOOL, "restore", [e], ms=t.ms())
    try:
        blob = args["blob"]
        ipsw = args["ipsw"]
        dry = args.get("dry_run", False)
        # Validate files exist before launching a long operation
        if not dry:
            for f, label in [(blob, "--blob"), (ipsw, "--ipsw")]:
                if not Path(f).exists():
                    return err(
                        TOOL, "restore", [f"{label} file not found: {f}"], ms=t.ms()
                    )
        exe = resolve_usb_exe("futurerestore")
        cmd = [exe, "--use-pwndfu", "-t", blob, ipsw]
        if dry:
            return ok(
                TOOL,
                "restore",
                {"dry_run": True, "cmd": [str(c) for c in cmd]},
                ms=t.ms(),
            )
        rc, out, er = run(cmd, timeout=600)
        if rc != 0:
            return err(TOOL, "restore", [er or out], ms=t.ms())
        return ok(TOOL, "restore", {"output": out, "rc": rc}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "restore", [str(e)], ms=t.ms())


HANDLERS = {"restore": cmd_restore}


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
