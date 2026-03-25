"""
nvidia_updater worker — check for newer NVIDIA drivers.
CLI path: sys nvidia check
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1] / "_lib"))
from common import Timer, current_os, emit, err, ok, resolve_usb_exe, run

TOOL = "nvidia_updater"


def _check():
    return "nvidia_updater is Windows-only." if current_os() != "windows" else None


def cmd_check(args: dict) -> dict:
    t = Timer()
    if e := _check():
        return err(TOOL, "check", [e], ms=t.ms())
    try:
        exe = resolve_usb_exe("nvidia_check")
        rc, out, er = run(
            [exe, "--no-browser"], timeout=30, dry_run=args.get("dry_run", False)
        )
        update_available = "new version" in (out + er).lower()
        return ok(
            TOOL,
            "check",
            {
                "output": out or er,
                "update_available": update_available,
                "rc": rc,
            },
            ms=t.ms(),
        )
    except Exception as e:
        return err(TOOL, "check", [str(e)], ms=t.ms())


HANDLERS = {"check": cmd_check}


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
