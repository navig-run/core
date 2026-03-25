"""
iperf3 worker — persistent process, handles commands via stdin JSON lines.
CLI path: net iperf3 client | net iperf3 server
"""
from __future__ import annotations
import json, shutil, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1] / "_lib"))
from common import ok, err, emit, run, Timer, current_os, resolve_usb_exe, require_on_path

TOOL = "iperf3"

def _find_iperf3() -> __import__("pathlib").Path:
    """USB binary on Windows, system binary elsewhere."""
    if current_os() == "windows":
        return resolve_usb_exe("iperf3")
    return require_on_path("iperf3")

def cmd_client(args: dict) -> dict:
    t = Timer()
    dry = args.get("dry_run", False)
    try:
        exe = _find_iperf3()
        cmd = [exe, "-c", args["host"], "-p", str(args.get("port", 5201)),
               "-t", str(args.get("duration", 10))]
        if args.get("json"):
            cmd.append("--json")
        rc, out, er = run(cmd, timeout=args.get("duration", 10) + 15, dry_run=dry)
        if rc != 0:
            return err(TOOL, "client", [f"iperf3 exited {rc}: {er}"], ms=t.ms())
        data = {"output": out, "host": args["host"], "port": args.get("port", 5201),
                "duration": args.get("duration", 10)}
        if args.get("json") and out.startswith("{"):
            try:
                data["iperf3_result"] = json.loads(out)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        return ok(TOOL, "client", data, ms=t.ms())
    except Exception as e:
        return err(TOOL, "client", [str(e)], ms=t.ms())

def cmd_server(args: dict) -> dict:
    t = Timer()
    try:
        exe = _find_iperf3()
        cmd = [exe, "-s", "-p", str(args.get("port", 5201))]
        if args.get("dry_run"):
            return ok(TOOL, "server", {"dry_run": True, "cmd": [str(c) for c in cmd]}, ms=t.ms())
        import subprocess
        proc = subprocess.Popen([str(c) for c in cmd])
        return ok(TOOL, "server", {"pid": proc.pid, "port": args.get("port", 5201),
                                    "note": "Server started. Kill PID to stop."}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "server", [str(e)], ms=t.ms())

HANDLERS = {"client": cmd_client, "server": cmd_server}

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            command = req.get("command", "")
            handler = HANDLERS.get(command)
            if not handler:
                emit(err(TOOL, command, [f"Unknown command: {command}"]))
            else:
                emit(handler(req.get("args", {})))
        except Exception as e:
            emit(err(TOOL, "?", [f"Parse error: {e}"]))

if __name__ == "__main__":
    main()
