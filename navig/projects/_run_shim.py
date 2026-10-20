"""Supervisor shim for one project script run — STDLIB ONLY, executed by path.

Why a shim instead of spawning the script straight from the daemon:

* **Exit codes for every run.** Only the process holding the ``Popen`` handle can
  read the exit code. The daemon restarts (``navig service restart`` after every
  core merge), the CLI exits right after starting a run — so neither can own it.
  The shim waits on the script and writes the code to its status file.
* **Survives a daemon restart.** ``taskkill /T`` on the daemon walks the tree by
  parent pid; a dev server spawned as the daemon's child dies with it. The shim
  is double-spawned (``--detach`` relaunches itself and exits at once), so its
  parent is gone and no tree walk from the daemon reaches it.
* **Logs never depend on a pipe.** Output goes straight into the log file; there
  is no reader whose death could block or break the script.

It must not import navig (it runs under a bare interpreter by file path, and a
heavy import would delay every start). Files:

  <run>.json         spec, written by the runner — read-only here
  <run>.status.json  written ONLY here: shim pid, child pid, exit code, timestamps
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_status(path: str, data: dict) -> None:
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    for _ in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # a reader holds it for a moment on Windows
            time.sleep(0.05)
    os.replace(tmp, path)


def _flags(new_group: bool) -> dict:
    if sys.platform != "win32":
        return {"start_new_session": True}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if new_group:
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return {"creationflags": flags}


def _detach(spec_path: str) -> int:
    """Relaunch as the real shim and return at once — its parent is then gone."""
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--child", spec_path],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, **_flags(new_group=True),
    )
    return 0


def _child(spec_path: str) -> int:
    with open(spec_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    status_path = spec["status_path"]
    status = {"shim_pid": os.getpid(), "state": "starting", "started_at": _now()}
    _write_status(status_path, status)

    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in (spec.get("env") or {}).items()})
    os.makedirs(os.path.dirname(spec["log_path"]), exist_ok=True)
    with open(spec["log_path"], "ab", buffering=0) as log:
        header = f"\n▶ {spec.get('label', '')}  ({' '.join(spec['argv'])})  in {spec['cwd']}  at {_now()}\n"
        log.write(header.encode("utf-8", "replace"))
        try:
            proc = subprocess.Popen(
                spec["argv"], cwd=spec["cwd"], env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                **_flags(new_group=False),
            )
        except OSError as exc:
            log.write(f"✗ could not start: {exc}\n".encode("utf-8", "replace"))
            status.update(state="failed", exit_code=None, error=str(exc), ended_at=_now())
            _write_status(status_path, status)
            return 1
        status.update(state="running", child_pid=proc.pid)
        _write_status(status_path, status)
        code = proc.wait()
        log.write(f"\n■ exited with code {code} at {_now()}\n".encode("utf-8", "replace"))
    status.update(state="exited" if code == 0 else "failed", exit_code=code, ended_at=_now())
    _write_status(status_path, status)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "--detach":
        return _detach(argv[1])
    if len(argv) == 2 and argv[0] == "--child":
        return _child(argv[1])
    print("usage: _run_shim.py (--detach|--child) <spec.json>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
