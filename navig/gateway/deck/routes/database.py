"""Database browser — list / tables / query against remote DBs over SSH.

Thin passthrough — defers to the same CLI dispatch the Telegram /db /tables
/query commands use. For v1 we surface:

  GET  /api/deck/db/hosts          → host list + which have DB containers
  POST /api/deck/db/list           → enumerate databases on a host
  POST /api/deck/db/tables         → list tables in a database
  POST /api/deck/db/query          → run a SQL statement, return rows/raw text

Body schema is the same across the POST routes:
  {"host": "myserver", "db_type": "mysql"|"postgres"|"sqlite", "database": "...",
   "user": "root", "password": "...", "query": "SELECT 1"}
"""

from __future__ import annotations

import logging
import shlex
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

from navig.gateway.deck.routes._utils import run_on_host

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


def _quote(s: str) -> str:
    return shlex.quote(s or "")


def _build_query_cmd(db_type: str, database: str | None, user: str, password: str | None, query: str) -> str:
    """Build a single shell command to execute the SQL via the native client."""
    db = (database or "").strip()
    pw = password or ""
    q = query.replace("\n", " ").strip()
    if db_type == "postgres" or db_type == "postgresql":
        # Use PGPASSWORD env, psql -c, default db "postgres" if none given
        return (
            f"PGPASSWORD={_quote(pw)} psql -U {_quote(user)} "
            f"{'-d ' + _quote(db) if db else ''} -c {_quote(q)}"
        )
    if db_type == "sqlite":
        return f"sqlite3 {_quote(db)} {_quote(q)}"
    # default: mysql
    return (
        f"mysql -u {_quote(user)} {('-p' + _quote(pw)) if pw else ''} "
        f"{_quote(db) if db else ''} -e {_quote(q)}"
    )


def _build_list_cmd(db_type: str, user: str, password: str | None) -> str:
    pw = password or ""
    if db_type in ("postgres", "postgresql"):
        return f"PGPASSWORD={_quote(pw)} psql -U {_quote(user)} -lqt"
    return f"mysql -u {_quote(user)} {('-p' + _quote(pw)) if pw else ''} -e 'SHOW DATABASES'"


def _build_tables_cmd(db_type: str, database: str, user: str, password: str | None) -> str:
    pw = password or ""
    if db_type in ("postgres", "postgresql"):
        return f"PGPASSWORD={_quote(pw)} psql -U {_quote(user)} -d {_quote(database)} -c '\\dt'"
    return f"mysql -u {_quote(user)} {('-p' + _quote(pw)) if pw else ''} -e 'SHOW TABLES' {_quote(database)}"


async def _run_remote(host: str, command: str, timeout: float = 45.0) -> tuple[bool, str, str]:
    """Run a shell command on a CONFIGURED `host` via the SSH executor, off the event loop.

    Delegates to the shared ``run_on_host`` runner (``_utils``). This wrapper used to be a
    byte-for-byte copy of ``remote._ssh`` — but with a copied bug: it once called
    ``ServerDiscovery(cfg, host_name=host)`` (a ConfigManager + a kwarg the constructor rejects), so
    every db list/tables/query raised and the console was dead while ``remote`` worked. One shared
    runner means that class of drift can't recur (#426). The 45s default (vs remote's 30s) stays —
    DB clients can be slow to connect.
    """
    return await run_on_host(host, command, timeout)


# ─── Endpoints ──────────────────────────────────────────────────────────────


async def handle_deck_db_hosts(request: "web.Request") -> "web.Response":
    """List configured hosts (a thin wrapper — the DB picker uses these)."""
    try:
        from navig.config import ConfigManager  # type: ignore[import]
        cfg = ConfigManager()
        names = list(cfg.list_hosts() or [])
        active_raw = cfg.get_active_host()
        active = active_raw if isinstance(active_raw, str) else (active_raw[0] if active_raw else None)
        return _ok({"hosts": names, "active": active})
    except Exception as exc:
        logger.exception("db hosts list failed")
        return _err(str(exc))


async def handle_deck_db_list(request: "web.Request") -> "web.Response":
    body = await _read_body(request)
    host = (body.get("host") or "").strip()
    db_type = (body.get("db_type") or "mysql").strip().lower()
    user = (body.get("user") or "root").strip()
    password = body.get("password")
    if not host:
        return _err("missing 'host'", status=400)
    cmd = _build_list_cmd(db_type, user, password)
    ok, out, err = await _run_remote(host, cmd)
    if not ok and not out:
        return _err(err or "ssh failed", status=502)
    return _ok({"host": host, "db_type": db_type, "raw": out, "warnings": err or ""})


async def handle_deck_db_tables(request: "web.Request") -> "web.Response":
    body = await _read_body(request)
    host = (body.get("host") or "").strip()
    database = (body.get("database") or "").strip()
    db_type = (body.get("db_type") or "mysql").strip().lower()
    user = (body.get("user") or "root").strip()
    password = body.get("password")
    if not host or not database:
        return _err("'host' and 'database' are required", status=400)
    cmd = _build_tables_cmd(db_type, database, user, password)
    ok, out, err = await _run_remote(host, cmd)
    if not ok and not out:
        return _err(err or "ssh failed", status=502)
    return _ok({"host": host, "database": database, "db_type": db_type, "raw": out, "warnings": err or ""})


async def handle_deck_db_query(request: "web.Request") -> "web.Response":
    body = await _read_body(request)
    host = (body.get("host") or "").strip()
    db_type = (body.get("db_type") or "mysql").strip().lower()
    database = body.get("database")
    user = (body.get("user") or "root").strip()
    password = body.get("password")
    query = (body.get("query") or "").strip()
    if not host or not query:
        return _err("'host' and 'query' are required", status=400)
    cmd = _build_query_cmd(db_type, database, user, password, query)
    ok, out, err = await _run_remote(host, cmd)
    return _ok({
        "host": host, "db_type": db_type, "database": database or "",
        "query": query, "ok": ok, "stdout": out, "stderr": err,
    })
