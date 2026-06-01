"""Remote — SSH-backed host operations exposed to the Deck.

Wraps `navig.discovery.ServerDiscovery._execute_ssh` for any-shell-command
remote exec, plus reads `cfg.list_hosts()` / `cfg.get_active_host()`.

Routes:
  GET  /api/deck/remote/hosts                list hosts + active
  POST /api/deck/remote/hosts/use            switch active host
  POST /api/deck/remote/hosts/test           ssh-probe a host
  GET  /api/deck/remote/files                list files in a path on host
  GET  /api/deck/remote/cat                  cat a file on host (truncated)
  POST /api/deck/remote/run                  run shell on host (timeout-capped)
  POST /api/deck/remote/docker               docker ps / logs / restart
  GET  /api/deck/remote/backup               read backup status snapshot
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _read_body(request: "web.Request") -> dict[str, Any]:
    try:
        return await request.json()
    except Exception:
        return {}


def _q(s: str) -> str:
    return shlex.quote(s or "")


async def _ssh(host: str, command: str, timeout: float = 30.0) -> tuple[bool, str, str]:
    """Run a command on `host` via the project SSH executor in a thread."""
    try:
        from navig.discovery import ServerDiscovery  # type: ignore[import]
        from navig.config import get_config_manager  # type: ignore[import]
    except Exception as exc:
        return False, "", f"navig backend unavailable: {exc}"
    cfg = get_config_manager()
    # ServerDiscovery takes the host's SSH config *dict* (host/user/port/key…),
    # not the ConfigManager. Load the named host's config first.
    try:
        if not cfg.host_exists(host):
            return False, "", f"host '{host}' not configured"
        ssh_config = cfg.load_host_config(host)
    except Exception as exc:
        return False, "", f"could not load host '{host}': {exc}"
    try:
        disco = ServerDiscovery(ssh_config)
    except Exception as exc:
        return False, "", f"host '{host}' invalid config: {exc}"
    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: disco._execute_ssh(command)),
            timeout=timeout,
        )
        if isinstance(result, tuple) and len(result) == 3:
            return result[0], result[1] or "", result[2] or ""
        return False, "", "unexpected ssh result shape"
    except asyncio.TimeoutError:
        return False, "", f"timed out after {timeout}s"
    except Exception as exc:
        return False, "", str(exc)


# ─── Hosts ───────────────────────────────────────────────────────────────────


async def handle_deck_remote_hosts(request: "web.Request") -> "web.Response":
    try:
        from navig.config import ConfigManager  # type: ignore[import]
        cfg = ConfigManager()
        names = list(cfg.list_hosts() or [])
        active_raw = cfg.get_active_host()
        active = active_raw if isinstance(active_raw, str) else (active_raw[0] if active_raw else None)
        return _ok({"hosts": names, "active": active})
    except Exception as exc:
        logger.exception("remote hosts list failed")
        return _err(str(exc))


async def handle_deck_remote_host_use(request: "web.Request") -> "web.Response":
    body = await _read_body(request)
    host = (body.get("host") or "").strip()
    if not host:
        return _err("'host' is required", status=400)
    try:
        from navig.config import get_config_manager  # type: ignore[import]
        cm = get_config_manager()
        gc = cm.global_config or {}
        gc.setdefault("hosts", {})["active"] = host
        cm.update_global_config(gc)
        return _ok({"active": host})
    except Exception as exc:
        logger.exception("host switch failed")
        return _err(str(exc))


async def handle_deck_remote_host_test(request: "web.Request") -> "web.Response":
    body = await _read_body(request)
    host = (body.get("host") or "").strip()
    if not host:
        return _err("'host' is required", status=400)
    ok, out, err = await _ssh(host, "echo navig-ssh-ok && uname -a && uptime", timeout=12)
    return _ok({"host": host, "ok": ok, "stdout": out.strip(), "stderr": err.strip()})


# ─── Filesystem ──────────────────────────────────────────────────────────────


async def handle_deck_remote_files(request: "web.Request") -> "web.Response":
    host = (request.query.get("host") or "").strip()
    path = (request.query.get("path") or ".").strip() or "."
    if not host:
        return _err("?host= required", status=400)
    # -A: show dotfiles; -l: long; -h: human sizes; sort by name
    cmd = f"ls -lAh --time-style=long-iso {_q(path)} 2>&1 | head -200"
    ok, out, err = await _ssh(host, cmd, timeout=15)
    return _ok({"host": host, "path": path, "ok": ok, "raw": out, "stderr": err})


async def handle_deck_remote_cat(request: "web.Request") -> "web.Response":
    host = (request.query.get("host") or "").strip()
    path = (request.query.get("path") or "").strip()
    try:
        max_kb = max(1, min(512, int(request.query.get("max_kb", 64))))
    except ValueError:
        max_kb = 64
    if not host or not path:
        return _err("?host= and ?path= required", status=400)
    # Truncate output to keep the response small.
    cmd = f"head -c {max_kb * 1024} {_q(path)}"
    ok, out, err = await _ssh(host, cmd, timeout=20)
    return _ok({"host": host, "path": path, "ok": ok, "content": out, "stderr": err,
                "truncated_at_kb": max_kb})


# ─── Shell exec ──────────────────────────────────────────────────────────────


async def handle_deck_remote_run(request: "web.Request") -> "web.Response":
    body = await _read_body(request)
    host = (body.get("host") or "").strip()
    command = (body.get("command") or "").strip()
    try:
        timeout = float(body.get("timeout") or 30)
    except (TypeError, ValueError):
        timeout = 30.0
    if not host or not command:
        return _err("'host' and 'command' required", status=400)
    ok, out, err = await _ssh(host, command, timeout=min(120.0, timeout))
    return _ok({"host": host, "command": command, "ok": ok, "stdout": out, "stderr": err})


# ─── Docker ──────────────────────────────────────────────────────────────────


async def handle_deck_remote_docker(request: "web.Request") -> "web.Response":
    body = await _read_body(request)
    host = (body.get("host") or "").strip()
    action = (body.get("action") or "ps").strip().lower()
    container = (body.get("container") or "").strip()
    if not host:
        return _err("'host' required", status=400)

    if action == "ps":
        cmd = "docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Image}}\\t{{.Ports}}' 2>&1"
    elif action == "logs":
        if not container:
            return _err("'container' required for logs", status=400)
        cmd = f"docker logs --tail=200 {_q(container)} 2>&1"
    elif action == "restart":
        if not container:
            return _err("'container' required for restart", status=400)
        cmd = f"docker restart {_q(container)} 2>&1"
    elif action == "stop":
        if not container:
            return _err("'container' required for stop", status=400)
        cmd = f"docker stop {_q(container)} 2>&1"
    elif action == "start":
        if not container:
            return _err("'container' required for start", status=400)
        cmd = f"docker start {_q(container)} 2>&1"
    else:
        return _err(f"unknown action '{action}'", status=400)

    ok, out, err = await _ssh(host, cmd, timeout=30)
    return _ok({"host": host, "action": action, "container": container,
                 "ok": ok, "stdout": out, "stderr": err})


# ─── Backup ──────────────────────────────────────────────────────────────────


async def handle_deck_remote_backup(request: "web.Request") -> "web.Response":
    host = (request.query.get("host") or "").strip()
    if not host:
        # Local-side backup status — best effort
        try:
            from pathlib import Path
            p = Path.home() / ".navig" / "backup" / "status.json"
            if p.exists():
                import json
                return _ok({"source": "local", "data": json.loads(p.read_text(encoding="utf-8"))})
        except Exception:
            pass
        return _ok({"source": "local", "data": None, "note": "No local backup status found."})
    # Remote: try a couple of well-known status paths
    cmd = "cat ~/.navig/backup/status.json 2>/dev/null || echo '{}'"
    ok, out, _err = await _ssh(host, cmd, timeout=10)
    try:
        import json
        data = json.loads(out or "{}")
    except Exception:
        data = {"raw": out}
    return _ok({"source": "remote", "host": host, "data": data})
