"""Project script runner — start, watch, stop the scripts a project declares.

Safety model (the deck is reachable remotely through Lighthouse):

* A run is requested by **script key** only. The command line is re-derived here
  from the project's own manifests (:func:`navig.projects.profile.build_profile`)
  — a client can name a script, never supply a command.
* Script names are checked against a conservative pattern before they reach a
  ``cmd.exe`` wrapper (npm shims are ``.cmd`` files on Windows), so a hostile
  ``package.json`` cannot smuggle ``&`` into a shell line.
* Starting/stopping is local-only — enforced by the routes (``is_local_request``).

Process model: every run is supervised by :mod:`navig.projects._run_shim`
(double-spawned, stdlib-only). State lives in two files per run under
``data_dir()/projects/runs/`` — the spec (written here) and the status (written
only by the shim) — so the daemon, the CLI and a restarted daemon all see the
same runs without sharing a process. Liveness is ``pid`` **and** process
``create_time``: a pid file is a claim, not proof (pids are recycled).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["MAX_CONCURRENT", "RunError", "ProjectRunner", "get_runner", "reset_runner"]

MAX_CONCURRENT = 12
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_@][A-Za-z0-9_.:@/+\-]{0,120}$")
_LOG_ROTATE_BYTES = 5 * 1024 * 1024
_TAIL_MAX = 256 * 1024
_KEEP_FINISHED_SECS = 7 * 86400
_PORT_TTL = 3.0
_CREATE_TIME_SLACK = 1.0


class RunError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(p: str | Path) -> str:
    try:
        return os.path.normcase(str(Path(p).resolve()))
    except OSError:
        return os.path.normcase(str(p))


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")[:80] or "x"


def _read_json(path: Path) -> dict[str, Any]:
    for _ in range(5):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (PermissionError, ValueError):
            time.sleep(0.03)  # mid-replace on Windows, or a half-written file
        except OSError:
            return {}
    return {}


# ── argv derivation (the only place a command line is built) ────────────────


def _which(name: str, cwd: Path) -> str | None:
    local = cwd / "node_modules" / ".bin"
    return shutil.which(name, path=os.pathsep.join([str(local), os.environ.get("PATH", "")]))


def _shell_wrap(exe: str, args: list[str]) -> list[str]:
    """The argv for *exe*, resolved to a full path.

    A Windows `.cmd` shim (npm, pnpm) is passed as-is: CreateProcess runs it through
    cmd.exe itself and quotes the path correctly. Wrapping it in ``cmd /s /c`` by hand
    breaks the moment the path has a space (``C:\\Program Files\\nodejs``) — /s strips
    the quotes around it. Arguments are only ever validated script names
    (:data:`_SAFE_NAME`), so nothing here can be read by cmd.exe as an operator.
    """
    return [exe, *args]


def derive_argv(root: Path, script: dict[str, Any]) -> tuple[list[str], Path]:
    """Build the argv and cwd for *script* from the project's own manifests."""
    name = str(script.get("name") or "")
    if not _SAFE_NAME.match(name):
        raise RunError(f"refusing to run a script named {name!r} (unsafe characters)")
    cwd = (root / script.get("cwd", "")).resolve() if script.get("cwd") else root.resolve()
    if not cwd.is_relative_to(root.resolve()):
        raise RunError("script folder escapes the project")
    kind = script.get("kind")
    if kind == "npm":
        pm = script.get("runner") or "npm"
        if pm not in ("npm", "pnpm", "yarn", "bun"):
            pm = "npm"
        exe = _which(pm, cwd)
        if not exe:
            raise RunError(f"{pm} is not installed or not on PATH", 424)
        return _shell_wrap(exe, ["run", name]), cwd
    if kind in ("make", "just"):
        exe = _which(kind, cwd)
        if not exe:
            raise RunError(f"{kind} is not installed or not on PATH", 424)
        return _shell_wrap(exe, [name]), cwd
    if kind == "python":
        for candidate in (root / ".venv" / "Scripts" / f"{name}.exe", root / ".venv" / "bin" / name):
            if candidate.is_file():
                return [str(candidate)], cwd
        exe = shutil.which(name)
        if not exe:
            raise RunError(f"'{name}' is not installed — install the project first (pip install -e .)", 424)
        return _shell_wrap(exe, []), cwd
    raise RunError(f"don't know how to run a {kind!r} script")


# ── process helpers ─────────────────────────────────────────────────────────


def _proc(pid: int | None, create_time: float | None) -> Any:
    """The live psutil process for (pid, create_time), or None — never a recycled pid."""
    if not pid:
        return None
    try:
        import psutil  # noqa: PLC0415

        p = psutil.Process(int(pid))
        if create_time is not None and abs(p.create_time() - float(create_time)) > _CREATE_TIME_SLACK:
            return None
        if p.status() == psutil.STATUS_ZOMBIE:
            return None
        return p
    except Exception:  # noqa: BLE001 — NoSuchProcess / AccessDenied / missing psutil
        return None


def _kill_tree(proc: Any, grace: float = 3.0) -> bool:
    """Terminate *proc* and every descendant; True when nothing of the tree is left."""
    import psutil  # noqa: PLC0415

    try:
        tree = [proc, *proc.children(recursive=True)]
    except psutil.Error:
        tree = [proc]
    for p in tree:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(tree, timeout=grace)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(alive, timeout=grace)
    return not alive


def _ports(proc: Any) -> list[int]:
    try:
        import psutil  # noqa: PLC0415

        pids = {proc.pid, *(c.pid for c in proc.children(recursive=True))}
        found = set()
        for conn in psutil.net_connections(kind="inet"):
            if conn.pid in pids and conn.status == psutil.CONN_LISTEN and conn.laddr:
                found.add(int(conn.laddr.port))
        return sorted(found)
    except Exception:  # noqa: BLE001 — ports are a convenience
        return []


# ── the runner ──────────────────────────────────────────────────────────────


class ProjectRunner:
    def __init__(self, base: Path | None = None) -> None:
        if base is None:
            from navig.platform import paths  # noqa: PLC0415

            base = paths.data_dir() / "projects" / "runs"
        self.base = Path(base)
        self._port_cache: dict[str, tuple[float, list[int]]] = {}

    # files
    def _spec_path(self, run_id: str) -> Path:
        return self.base / f"{run_id}.json"

    def _status_path(self, run_id: str) -> Path:
        return self.base / f"{run_id}.status.json"

    def _write_spec(self, spec: dict[str, Any]) -> None:
        from navig.core.json_io import atomic_write_json  # noqa: PLC0415

        atomic_write_json(spec, self._spec_path(spec["id"]))

    # views
    def _view(self, spec: dict[str, Any]) -> dict[str, Any]:
        status = _read_json(self._status_path(spec["id"]))
        state = status.get("state") or "starting"
        shim = _proc(status.get("shim_pid"), status.get("shim_create_time") or spec.get("shim_create_time"))
        if state in ("starting", "running") and shim is None:
            age = time.time() - float(spec.get("created_ts") or 0)
            if status.get("shim_pid") or age > 15:
                state = "stopped" if spec.get("stop_requested") else "lost"
        ports: list[int] = []
        if state == "running" and shim is not None and spec.get("long_running"):
            hit = self._port_cache.get(spec["id"])
            if hit and time.monotonic() - hit[0] < _PORT_TTL:
                ports = hit[1]
            else:
                ports = _ports(shim)
                self._port_cache[spec["id"]] = (time.monotonic(), ports)
        return {
            "id": spec["id"],
            "project_id": spec.get("project_id"),
            "cwd_root": spec.get("root"),
            "script_key": spec.get("script_key"),
            "name": spec.get("name"),
            "label": spec.get("label"),
            "argv": spec.get("argv"),
            "cwd": spec.get("cwd"),
            "state": state,
            "exit_code": status.get("exit_code"),
            "error": status.get("error"),
            "started_at": status.get("started_at") or spec.get("created_at"),
            "ended_at": status.get("ended_at"),
            "ports": ports,
            "danger": bool(spec.get("danger")),
            "log_path": spec.get("log_path"),
        }

    def _specs(self) -> list[dict[str, Any]]:
        if not self.base.is_dir():
            return []
        out = []
        for p in self.base.glob("*.json"):
            if p.name.endswith(".status.json"):
                continue
            spec = _read_json(p)
            if spec.get("id"):
                out.append(spec)
        return out

    def list_runs(self, *, root: str | Path | None = None, include_finished: bool = True) -> list[dict[str, Any]]:
        want = _norm(root) if root else None
        views = []
        for spec in self._specs():
            if want and _norm(spec.get("root", "")) != want:
                continue
            v = self._view(spec)
            if v["state"] not in ("starting", "running") and not spec.get("final_recorded"):
                # Lazily finalise a run that ended on its own: record its outcome once.
                # Any process may get here first; the history row is keyed by run id.
                self._record_history(spec, v)
                spec["final_recorded"] = True
                try:
                    self._write_spec(spec)
                except OSError:
                    logger.debug("could not mark run %s final", spec["id"], exc_info=True)
            if not include_finished and v["state"] not in ("starting", "running"):
                continue
            views.append(v)
        views.sort(key=lambda v: v.get("started_at") or "", reverse=True)
        return views

    def get(self, run_id: str) -> dict[str, Any]:
        spec = _read_json(self._spec_path(_slug(run_id)))
        if not spec.get("id"):
            raise RunError("unknown run", 404)
        return self._view(spec)

    # actions
    def start(self, project_id: str, root: Path, script_key: str) -> dict[str, Any]:
        from navig.platform import paths  # noqa: PLC0415
        from navig.projects.profile import build_profile, find_script  # noqa: PLC0415

        root = Path(root).resolve()
        script = find_script(build_profile(root), script_key)
        if script is None:
            raise RunError(f"no script {script_key!r} in this project", 404)
        for v in self.list_runs(root=root, include_finished=False):
            if v["script_key"] == script_key:
                return {**v, "already_running": True}
        if len(self.list_runs(include_finished=False)) >= MAX_CONCURRENT:
            raise RunError(f"{MAX_CONCURRENT} scripts are already running — stop one first", 429)
        argv, cwd = derive_argv(root, script)

        run_id = f"{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        log_path = paths.log_dir() / "projects" / _slug(project_id) / f"{_slug(script_key)}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.is_file() and log_path.stat().st_size > _LOG_ROTATE_BYTES:
            try:
                os.replace(log_path, log_path.with_suffix(".log.1"))
            except OSError:
                logger.debug("log rotate failed for %s", log_path, exc_info=True)
        self.base.mkdir(parents=True, exist_ok=True)
        spec = {
            "id": run_id, "project_id": project_id, "root": str(root), "script_key": script_key,
            "name": script["name"], "label": f"{project_id} · {script.get('cwd') or '.'} · {script['name']}",
            "argv": argv, "cwd": str(cwd), "log_path": str(log_path),
            "status_path": str(self._status_path(run_id)),
            # Colour for the log pane — unless the operator opted out (NO_COLOR): setting both
            # makes node print a warning into every run's log.
            "env": {**({} if os.environ.get("NO_COLOR") else {"FORCE_COLOR": "1"}),
                    "BROWSER": "none", "NAVIG_PROJECT": project_id},
            "danger": bool(script.get("danger")), "long_running": bool(script.get("long_running")),
            "created_at": _now(), "created_ts": time.time(),
        }
        self._write_spec(spec)
        shim = Path(__file__).with_name("_run_shim.py")
        from navig.platform.process import creation_flags  # noqa: PLC0415

        kwargs: dict[str, Any] = (
            {"creationflags": creation_flags(new_group=True)} if sys.platform == "win32"
            else {"start_new_session": True}
        )
        subprocess.run(
            [sys.executable, str(shim), "--detach", str(self._spec_path(run_id))],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, check=False, **kwargs,
        )
        # Pin the shim's identity (pid + create_time) as soon as it reports in.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = _read_json(self._status_path(run_id))
            if status.get("shim_pid"):
                p = _proc(status["shim_pid"], None)
                if p is not None:
                    try:
                        spec["shim_create_time"] = p.create_time()
                        self._write_spec(spec)
                    except Exception:  # noqa: BLE001
                        logger.debug("could not pin shim create_time", exc_info=True)
                break
            time.sleep(0.05)
        self._record_history(spec)
        return self._view(spec)

    def stop(self, run_id: str) -> dict[str, Any]:
        spec = _read_json(self._spec_path(_slug(run_id)))
        if not spec.get("id"):
            raise RunError("unknown run", 404)
        spec["stop_requested"] = True
        self._write_spec(spec)
        status = _read_json(self._status_path(spec["id"]))
        shim = _proc(status.get("shim_pid"), spec.get("shim_create_time"))
        killed = True
        if shim is not None:
            killed = _kill_tree(shim)
        else:
            child = _proc(status.get("child_pid"), None)
            if child is not None and status.get("state") == "running":
                killed = _kill_tree(child)
        view = self._view(spec)
        if not killed:
            raise RunError("could not stop every process of this run — check Task Manager", 500)
        self._record_history(spec, view)
        return view

    def tail(self, run_id: str, cursor: int | None = None, max_bytes: int = 64 * 1024) -> dict[str, Any]:
        view = self.get(run_id)
        path = Path(view["log_path"] or "")
        size = path.stat().st_size if path.is_file() else 0
        max_bytes = max(1024, min(int(max_bytes), _TAIL_MAX))
        start = max(0, size - max_bytes) if cursor is None or cursor > size else int(cursor)
        start = max(start, size - _TAIL_MAX)
        data = b""
        if size and start < size:
            with path.open("rb") as fh:
                fh.seek(start)
                data = fh.read(min(size - start, _TAIL_MAX))
        return {"text": data.decode("utf-8", "replace"), "cursor": start + len(data), "size": size,
                "state": view["state"], "exit_code": view["exit_code"], "truncated": start > 0 and cursor is None}

    def prune(self) -> int:
        """Forget finished runs older than a week (their logs stay)."""
        removed = 0
        cutoff = time.time() - _KEEP_FINISHED_SECS
        for spec in self._specs():
            v = self._view(spec)
            if v["state"] in ("running", "starting") or float(spec.get("created_ts") or 0) > cutoff:
                continue
            for p in (self._spec_path(spec["id"]), self._status_path(spec["id"])):
                try:
                    p.unlink()
                except OSError:
                    pass
            removed += 1
        return removed

    def _record_history(self, spec: dict[str, Any], view: dict[str, Any] | None = None) -> None:
        try:
            from navig.projects.store import get_project_store  # noqa: PLC0415

            v = view or self._view(spec)
            get_project_store().record_run(
                spec["root"], spec["script_key"], started_at=spec["created_at"], ended_at=v.get("ended_at"),
                exit_code=v.get("exit_code"), outcome=v["state"], run_id=spec["id"],
            )
        except Exception:  # noqa: BLE001 — history is a convenience
            logger.debug("run history write failed", exc_info=True)


_runner: ProjectRunner | None = None


def get_runner() -> ProjectRunner:
    global _runner
    if _runner is None:
        _runner = ProjectRunner()
    return _runner


def reset_runner() -> None:
    global _runner
    _runner = None
