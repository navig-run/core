"""Maintenance log tailing for the Deck console.

Exposes the navig log files (daemon, telegram, gateway, …) for the deck's live
log viewer via cheap byte-offset incremental reads.

    GET /api/deck/logs/sources                 → available log files
    GET /api/deck/logs?source=&cursor=&max=    → incremental tail

Registered in ``navig/gateway/deck/__init__.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)

_MAX_READ = 256 * 1024
_DEFAULT_READ = 64 * 1024


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


def _sources() -> dict[str, tuple[str, Path]]:
    """id → (label, path). Resolved fresh so env overrides apply."""
    from navig.platform import paths

    log = paths.log_dir()
    cfg = paths.config_dir()
    return {
        "daemon": ("Daemon", log / "daemon.log"),
        "gateway": ("Gateway", log / "gateway.log"),
        "telegram": ("Telegram", log / "telegram.log"),
        "children": ("Children", log / "children.log"),
        "debug": ("Debug", log / "debug.log"),
        "navig": ("App", cfg / "navig.log"),
        "audit": ("Audit", cfg / "runtime" / "audit.jsonl"),
    }


async def handle_deck_logs_sources(request: "web.Request") -> "web.Response":
    """List available log sources with existence + size."""
    out = []
    for sid, (label, path) in _sources().items():
        try:
            exists = path.is_file()
            size = path.stat().st_size if exists else 0
        except Exception:
            exists, size = False, 0
        out.append({"id": sid, "label": label, "path": str(path), "exists": exists, "size": size})
    return _ok({"sources": out})


async def handle_deck_logs_tail(request: "web.Request") -> "web.Response":
    """Incremental tail. cursor<0 seeds from the end; cursor>size resets (rotation)."""
    q = request.rel_url.query
    sid = q.get("source", "")
    src = _sources().get(sid)
    if src is None:
        return _err(f"unknown log source '{sid}'", 400)

    try:
        cursor = int(q.get("cursor", "-1"))
    except ValueError:
        cursor = -1
    try:
        max_read = min(int(q.get("max", str(_DEFAULT_READ))), _MAX_READ)
    except ValueError:
        max_read = _DEFAULT_READ
    max_read = max(1024, max_read)

    path = src[1]
    if not path.is_file():
        return _ok({"lines": [], "cursor": 0, "eof": True, "exists": False, "size": 0})

    try:
        size = path.stat().st_size
        if cursor < 0:
            start = max(0, size - max_read)
        elif cursor > size:  # file rotated/truncated since last poll
            start = 0
        else:
            start = cursor
        drop_lead = cursor < 0 and start > 0

        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(max_read)
    except Exception as exc:
        logger.debug("log tail read failed: %s", exc, exc_info=True)
        return _err(str(exc))

    # Only emit complete lines (up to the last newline) so we never show a
    # half-written trailing line; the remainder is picked up next poll.
    nl = data.rfind(b"\n")
    consumed = data[: nl + 1] if nl != -1 else b""
    new_cursor = start + len(consumed)

    text = consumed.decode("utf-8", errors="replace")
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    if drop_lead and lines:
        lines = lines[1:]  # the seek landed mid-line; drop the partial head

    return _ok({"lines": lines, "cursor": new_cursor, "eof": new_cursor >= size, "exists": True, "size": size})
