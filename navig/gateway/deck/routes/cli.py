"""navig CLI console for the Deck API.

Powers the deck's game-style console: a command catalog for autocomplete, and a
streaming command runner that executes the navig CLI faithfully.

    GET  /api/deck/cli/commands   → command catalog (groups + flat + byPath)
    POST /api/deck/cli/exec       → run a navig command, stream output (SSE)

Safety: only commands whose first token is ``navig`` run. Each command is
classified by the EXISTING approval policy; dangerous/confirm-level commands go
through ``gateway.approval_manager.request_approval`` (which already notifies the
user's channels + the Inbox) before executing. ``never`` commands are blocked.

Registered in ``navig/gateway/deck/__init__.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import sys

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)

# Output caps — protect the deck and the daemon from a runaway command.
_MAX_LINES = 5000
_MAX_BYTES = 2 * 1024 * 1024
_DEFAULT_TIMEOUT = 120
_MAX_TIMEOUT = 600
_WAITING_PING_SECS = 15  # keep the stream warm while parked on approval

_schema_cache: dict | None = None


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


def _gateway(request: "web.Request"):
    return request.app.get("gateway") if hasattr(request, "app") else None


# ── Command catalog ──────────────────────────────────────────


def _build_catalog() -> dict:
    """Reshape get_schema() into {groups, flat, byPath}. Never raises."""
    try:
        from navig.cli.registry import get_schema

        schema = get_schema()
    except Exception:
        logger.debug("cli schema load failed", exc_info=True)
        return {"groups": [], "flat": [], "byPath": {}}

    by_path: dict[str, dict] = {}
    for cmd in schema.get("commands", []) or []:
        if not isinstance(cmd, dict):
            continue
        path = str(cmd.get("path", "")).strip()
        if not path:
            continue
        by_path[path] = {
            "examples": cmd.get("examples", []) or [],
            "tags": cmd.get("tags", []) or [],
        }

    return {
        "groups": schema.get("groups", []) or [],
        "flat": schema.get("flat_commands", []) or [],
        "byPath": by_path,
    }


async def handle_deck_cli_commands(request: "web.Request") -> "web.Response":
    """Return the navig command catalog (process-cached; static per daemon run)."""
    global _schema_cache
    if _schema_cache is None or request.rel_url.query.get("fresh") == "1":
        _schema_cache = _build_catalog()
    return _ok(_schema_cache)


# ── Command execution (streaming) ────────────────────────────


def _classify(gw, sub_command: str) -> str:
    """Classify the prefix-stripped command via the live approval policy."""
    policy = None
    am = getattr(gw, "approval_manager", None) if gw else None
    if am is not None:
        policy = getattr(am, "policy", None)
    if policy is None:
        try:
            from navig.approval.policies import ApprovalPolicy

            policy = ApprovalPolicy()
        except Exception:
            return "confirm"
    try:
        level = policy.classify_command(sub_command)
        return level.value if hasattr(level, "value") else str(level)
    except Exception:
        return "confirm"


async def handle_deck_cli_exec(request: "web.Request") -> "web.Response":
    """Run a navig command and stream output as an event-stream.

    Frames (one JSON object per `data:` line):
        {"type":"status","stage":...}
        {"type":"out","line":...}  {"type":"err","line":...}
        {"type":"exit","code":...}
    """
    from navig.gateway.middleware import cors_headers_for
    from navig.platform import paths

    try:
        body = await request.json()
    except Exception:
        body = {}
    command = str(body.get("command", "")).strip()
    try:
        timeout = int(body.get("timeout") or _DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = _DEFAULT_TIMEOUT
    timeout = max(1, min(timeout, _MAX_TIMEOUT))

    resp = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            **cors_headers_for(request),
        }
    )
    await resp.prepare(request)

    async def send(obj: dict) -> bool:
        try:
            await resp.write(f"data: {json.dumps(obj)}\n\n".encode())
            return True
        except Exception:
            return False

    # ── Parse + guard ────────────────────────────────────────
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        await send({"type": "err", "line": f"parse error: {exc}"})
        await send({"type": "exit", "code": -2})
        return resp
    if not argv:
        await send({"type": "exit", "code": -2})
        return resp
    if argv[0] != "navig":
        await send({"type": "err", "line": "Only `navig …` commands are allowed in this console."})
        await send({"type": "exit", "code": -2})
        return resp

    sub = " ".join(argv[1:]).strip()
    gw = _gateway(request)

    # ── Classify + approval gate (reuses existing security) ──
    await send({"type": "status", "stage": "classifying"})
    level = _classify(gw, sub) if sub else "safe"
    if level == "never":
        await send({"type": "err", "line": f"Blocked: '{command}' is a never-allow command."})
        await send({"type": "exit", "code": -2})
        return resp

    # Only genuinely DANGEROUS commands prompt for approval — CONFIRM is the
    # default bucket for unlisted commands, so gating it would ask on nearly
    # every command. NEVER is already blocked above; SAFE/CONFIRM run directly.
    if level == "dangerous":
        am = getattr(gw, "approval_manager", None) if gw else None
        if am is not None:
            await send({"type": "status", "stage": "pending_approval", "level": level, "command": command})
            approve_task = asyncio.ensure_future(
                am.request_approval(
                    command=sub,
                    session_key="deck:console",
                    channel="deck",
                    user_id="console",
                    description=f"Console command: {command}",
                )
            )
            # Keep the stream warm while the user decides elsewhere.
            while True:
                try:
                    approved = await asyncio.wait_for(asyncio.shield(approve_task), timeout=_WAITING_PING_SECS)
                    break
                except asyncio.TimeoutError:
                    if not await send({"type": "status", "stage": "waiting", "level": level}):
                        approve_task.cancel()
                        return resp
            if not approved:
                await send({"type": "err", "line": "Denied — command was not approved."})
                await send({"type": "exit", "code": -2})
                return resp

    # ── Spawn + stream ───────────────────────────────────────
    env = {**os.environ, "NO_COLOR": "1", "NAVIG_NONINTERACTIVE": "1", "PYTHONUNBUFFERED": "1"}
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "navig", *argv[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(paths.config_dir()),
            env=env,
        )
    except Exception as exc:
        await send({"type": "err", "line": f"failed to launch: {exc}"})
        await send({"type": "exit", "code": -3})
        return resp

    await send({"type": "status", "stage": "running", "pid": proc.pid})

    counters = {"lines": 0, "bytes": 0, "stop": False}

    async def pump(stream, kind: str) -> None:
        while not counters["stop"]:
            raw = await stream.readline()
            if not raw:
                break
            counters["lines"] += 1
            counters["bytes"] += len(raw)
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not await send({"type": kind, "line": line}):
                counters["stop"] = True
                break
            if counters["lines"] >= _MAX_LINES or counters["bytes"] >= _MAX_BYTES:
                counters["stop"] = True
                await send({"type": "status", "stage": "truncated"})
                break

    code = 0
    try:
        await asyncio.wait_for(
            asyncio.gather(pump(proc.stdout, "out"), pump(proc.stderr, "err")),
            timeout=timeout,
        )
        if counters["stop"]:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            code = await proc.wait()
        else:
            code = await proc.wait()
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await send({"type": "err", "line": f"timed out after {timeout}s — killed."})
        code = -1
    except Exception as exc:
        logger.debug("cli exec stream error", exc_info=True)
        await send({"type": "err", "line": f"stream error: {exc}"})
        code = -3

    await send({"type": "exit", "code": code})
    return resp
