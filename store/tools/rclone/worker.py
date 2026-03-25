"""
rclone worker — persistent process.
CLI path: cloud rclone ls | sync | copy | remotes
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1] / "_lib"))
from common import (
    Timer,
    current_os,
    emit,
    err,
    ok,
    require_on_path,
    resolve_usb_exe,
    run,
)

TOOL = "rclone"


def _find_rclone():
    if current_os() == "windows":
        try:
            return resolve_usb_exe("rclone")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
    return require_on_path("rclone")


def cmd_ls(args: dict) -> dict:
    t = Timer()
    try:
        exe = _find_rclone()
        remote = args["remote"]
        path = args.get("path", "")
        target = f"{remote}:{path}"
        rc, out, er = run([exe, "ls", target], dry_run=args.get("dry_run", False))
        if rc != 0:
            return err(TOOL, "ls", [f"rclone exited {rc}: {er}"], ms=t.ms())
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        files = []
        for l in lines:
            parts = l.split(None, 1)
            files.append(
                {
                    "size": int(parts[0]) if parts[0].isdigit() else 0,
                    "name": parts[1] if len(parts) > 1 else parts[0],
                }
            )
        return ok(
            TOOL,
            "ls",
            {"remote": target, "files": files, "count": len(files)},
            ms=t.ms(),
        )
    except Exception as e:
        return err(TOOL, "ls", [str(e)], ms=t.ms())


def cmd_sync(args: dict) -> dict:
    t = Timer()
    try:
        exe = _find_rclone()
        cmd = [exe, "sync", args["source"], args["destination"], "-P"]
        if args.get("dry_run"):
            cmd.insert(2, "--dry-run")
        rc, out, er = run(cmd, timeout=300)
        if rc != 0:
            return err(TOOL, "sync", [er or out], ms=t.ms())
        return ok(
            TOOL,
            "sync",
            {
                "source": args["source"],
                "destination": args["destination"],
                "output": out,
                "dry_run": args.get("dry_run", False),
            },
            ms=t.ms(),
        )
    except Exception as e:
        return err(TOOL, "sync", [str(e)], ms=t.ms())


def cmd_copy(args: dict) -> dict:
    t = Timer()
    try:
        exe = _find_rclone()
        cmd = [exe, "copy", args["source"], args["destination"], "-P"]
        if args.get("dry_run"):
            cmd.insert(2, "--dry-run")
        rc, out, er = run(cmd, timeout=300)
        if rc != 0:
            return err(TOOL, "copy", [er or out], ms=t.ms())
        return ok(
            TOOL,
            "copy",
            {
                "source": args["source"],
                "destination": args["destination"],
                "output": out,
            },
            ms=t.ms(),
        )
    except Exception as e:
        return err(TOOL, "copy", [str(e)], ms=t.ms())


def cmd_remotes(args: dict) -> dict:
    t = Timer()
    try:
        exe = _find_rclone()
        rc, out, er = run([exe, "listremotes"])
        if rc != 0:
            return err(TOOL, "remotes", [er], ms=t.ms())
        remotes = [r.rstrip(":") for r in out.splitlines() if r.strip()]
        return ok(
            TOOL, "remotes", {"remotes": remotes, "count": len(remotes)}, ms=t.ms()
        )
    except Exception as e:
        return err(TOOL, "remotes", [str(e)], ms=t.ms())


HANDLERS = {"ls": cmd_ls, "sync": cmd_sync, "copy": cmd_copy, "remotes": cmd_remotes}


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
