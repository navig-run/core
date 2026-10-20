"""Database browser — list / tables / query against remote DBs over SSH.

Thin passthrough — defers to the same CLI dispatch the Telegram /db /tables
/query commands use. For v1 we surface:

  GET  /api/deck/db/hosts          → host list + which have DB containers
  POST /api/deck/db/list           → enumerate databases on a host
  POST /api/deck/db/tables         → list tables in a database
  POST /api/deck/db/query          → run a SQL statement, return rows/raw text

A statement that is not proven read-only (``navig.core.sql_classify``, the classifier
``navig db query`` uses) is refused with 409 ``confirmation_required`` unless the body
carries ``"confirm": true``; every confirmed write is recorded in the gateway audit log
(``navig audit tail``) with its statement keywords, never its text.

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

from navig.gateway.deck.routes._enforce import audit_log_for, authorize_deck, deck_actor
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


def _secret_stdin(cmd: str, db_type: str, password: str | None) -> tuple[str, str | None]:
    """Move *password* out of the command line: stdin → MYSQL_PWD / PGPASSWORD.

    The command line here is not only the ssh argv — it is also handed to
    ``authorize_deck(command=…)``, i.e. the policy check and the audit log. With
    ``-p<pw>`` / ``PGPASSWORD=<pw>`` inline, every db console call wrote the
    password there.
    """
    from navig.core.remote_secret_env import with_secret_env  # noqa: PLC0415

    var = "PGPASSWORD" if db_type in ("postgres", "postgresql") else "MYSQL_PWD"
    return with_secret_env(cmd, {var: password})


def _build_query_cmd(db_type: str, database: str | None, user: str, query: str) -> str:
    """Build a single shell command to execute the SQL via the native client.

    Carries no password — :func:`_secret_stdin` adds it at execution time, so
    this string is safe for the policy check and the audit log.
    """
    db = (database or "").strip()
    q = query.replace("\n", " ").strip()
    if db_type == "postgres" or db_type == "postgresql":
        # psql -c, default db "postgres" if none given
        return f"psql -U {_quote(user)} {'-d ' + _quote(db) if db else ''} -c {_quote(q)}"
    if db_type == "sqlite":
        return f"sqlite3 {_quote(db)} {_quote(q)}"
    # default: mysql
    return f"mysql -u {_quote(user)} {_quote(db) if db else ''} -e {_quote(q)}"


def _build_list_cmd(db_type: str, user: str) -> str:
    if db_type in ("postgres", "postgresql"):
        return f"psql -U {_quote(user)} -lqt"
    return f"mysql -u {_quote(user)} -e 'SHOW DATABASES'"


def _build_tables_cmd(db_type: str, database: str, user: str) -> str:
    if db_type in ("postgres", "postgresql"):
        return f"psql -U {_quote(user)} -d {_quote(database)} -c '\\dt'"
    return f"mysql -u {_quote(user)} -e 'SHOW TABLES' {_quote(database)}"


async def _run_remote(
    host: str, command: str, timeout: float = 45.0, stdin: str | None = None
) -> tuple[bool, str, str]:
    """Run a shell command on a CONFIGURED `host` via the SSH executor, off the event loop.

    Delegates to the shared ``run_on_host`` runner (``_utils``). This wrapper used to be a
    byte-for-byte copy of ``remote._ssh`` — but with a copied bug: it once called
    ``ServerDiscovery(cfg, host_name=host)`` (a ConfigManager + a kwarg the constructor rejects), so
    every db list/tables/query raised and the console was dead while ``remote`` worked. One shared
    runner means that class of drift can't recur (#426). The 45s default (vs remote's 30s) stays —
    DB clients can be slow to connect.
    """
    if stdin is None:
        return await run_on_host(host, command, timeout)
    return await run_on_host(host, command, timeout, stdin=stdin)


async def _run_db(host: str, command: str, stdin: str | None) -> tuple[bool, str, str]:
    """Run a db client command; ``stdin`` (the password) only when there is one."""
    if stdin is None:
        return await _run_remote(host, command)
    return await _run_remote(host, command, stdin=stdin)

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
    cmd, stdin = _secret_stdin(_build_list_cmd(db_type, user), db_type, password)
    ok, out, err = await _run_db(host, cmd, stdin)
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
    cmd, stdin = _secret_stdin(_build_tables_cmd(db_type, database, user), db_type, password)
    ok, out, err = await _run_db(host, cmd, stdin)
    if not ok and not out:
        return _err(err or "ssh failed", status=502)
    return _ok({"host": host, "database": database, "db_type": db_type, "raw": out, "warnings": err or ""})


def _actor(request: "web.Request") -> str:
    """Who is asking — the shared server-derived identity in ``_enforce``."""
    return deck_actor(request)


def _audit_log(request: "web.Request"):
    """The gateway audit log — the shared accessor in ``_enforce``."""
    return audit_log_for(request)


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

    from navig.core.sql_classify import classify_sql, statement_keywords

    level = classify_sql(query)
    statements = statement_keywords(query)
    if level != "verbose" and body.get("confirm") is not True:
        # Nothing ran. The client shows what would change and re-sends with confirm.
        return web.json_response(
            {
                "ok": False,
                "error": "confirmation_required",
                "requires_confirmation": True,
                "level": level,
                "statements": statements,
            },
            status=409,
        )

    cmd = _build_query_cmd(db_type, database, user, query)
    # AFTER the confirm flow (an unconfirmed write never reaches policy) and before
    # any SSH: policy → approval → host lock → audit, server-derived actor.
    block = await authorize_deck(
        request, "db.query", raw_input=query, host=host, command=cmd
    )
    if block is not None:
        return block
    run_cmd, stdin = _secret_stdin(cmd, db_type, password)
    ok, out, err = await _run_db(host, run_cmd, stdin)
    audited: bool | None = None  # None = a read, which is not audited
    if level != "verbose":
        audited = False
        try:
            _audit_log(request).record(
                actor=_actor(request),
                action="db.query",
                policy="require_approval",
                status="success" if ok else "error",
                raw_input=query,  # hashed by AuditLog, never stored
                raw_output=out,
                metadata={
                    "host": host,
                    "db_type": db_type,
                    "database": database or "",
                    "level": level,
                    "statements": statements,
                    "confirmed": True,
                },
            )
            audited = True
        except Exception:  # noqa: BLE001 — the write already ran: report it unaudited, not a 500
            logger.exception("db.query audit record failed")
    return _ok({
        "host": host, "db_type": db_type, "database": database or "",
        "query": query, "ok": ok, "stdout": out, "stderr": err,
        "level": level, "audited": audited,
    })
