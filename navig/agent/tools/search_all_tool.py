"""navig.agent.tools.search_all_tool — one search across everything the user owns.

``search_all`` asks every source the user allows, in parallel, and returns one merged,
source-labelled list plus a status line per source:

* **connectors** — connected accounts (Gmail, Calendar, Drive, Notion, …), when
  ``ai.sources.connectors`` is on;
* **library** — the wiki, the encrypted Cabinet (only while unlocked), past chats and
  Telegram transcripts, when ``ai.sources.library`` is on. Past chats and transcripts
  are memory: skipped while memory is off or the chat is incognito;
* **recall** — Navig Recall's records from past AI sessions, once ``navig recall enable``
  has built its index (memory: skipped while memory is off or the chat is incognito);
* **web** — only when asked (``include_web``) and ``ai.web_search.mode`` is not ``off``.

Everything except the web runs on this machine. Each source has its own deadline; a
source that times out, is locked or fails is REPORTED in ``sources``, never silently
dropped, so the model can tell "nothing found" from "couldn't look".

Source contract (also for recall): ``async (query, limit) -> (rows, status)``, rows
``{source, title, snippet, ref, ts?}``, must not raise past the per-source guard.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

SOURCE_TIMEOUT_S = 5.0
_PER_SOURCE_LIMIT = 5
_MAX_TOTAL = 30

Row = dict[str, Any]
Source = Callable[[str, int], Awaitable[tuple[list[Row], str]]]


# ── sources ──────────────────────────────────────────────────────────────────


async def _connectors(query: str, limit: int) -> tuple[list[Row], str]:
    from navig.connectors.connected import search_connected

    found, status = await search_connected(query, limit=limit, timeout=SOURCE_TIMEOUT_S - 0.5)
    rows = [
        {
            "source": f"connector:{r.get('source', '?')}",
            "title": r.get("title", ""),
            "snippet": (r.get("preview") or "")[:300],
            "ref": r.get("url") or r.get("id", ""),
            "ts": r.get("timestamp"),
        }
        for r in found
    ]
    if not status:
        return rows, "no connected accounts"
    detail = ", ".join(f"{k} {v}" for k, v in sorted(status.items()))
    return rows, f"ok ({detail})"


async def _wiki(query: str, limit: int) -> tuple[list[Row], str]:
    from navig.agent.tools.wiki_tools import search as wiki_search

    hits = await asyncio.to_thread(wiki_search, query, None, limit)
    return [
        {"source": "wiki", "title": h.get("title", ""), "snippet": h.get("excerpt", ""), "ref": h.get("path", "")}
        for h in hits
    ], "ok"


async def _cabinet(query: str, limit: int) -> tuple[list[Row], str]:
    try:
        from navig.modules.registry import get_registry

        if not get_registry().is_enabled("cabinet"):
            return [], "plugin off"
        from navig_cabinet.search import search as cabinet_search
    except ImportError:
        return [], "not installed"
    out = await asyncio.to_thread(cabinet_search, query, limit)
    status = out.get("status", "ok")
    if status == "locked":
        return [], "locked — unlock the Cabinet to include it"
    if status == "missing":
        return [], "no cabinet yet"
    return [
        {"source": "cabinet", "title": i.get("title", ""), "snippet": i.get("snippet", ""), "ref": f"cabinet:{i.get('id', '')}"}
        for i in out.get("items", [])
    ], "ok"


async def _past_chats(query: str, limit: int) -> tuple[list[Row], str]:
    def _search() -> list[dict]:
        from navig.memory.conversation import ConversationStore
        from navig.platform import paths

        return ConversationStore(paths.data_dir() / "memory.db").fts_search(query, limit=limit)

    hits = await asyncio.to_thread(_search)
    rows = []
    for h in hits:
        msg = h.get("message")
        rows.append(
            {
                "source": "past-chat",
                "title": f"{getattr(msg, 'role', '')} in {getattr(msg, 'session_key', '')}".strip(),
                "snippet": h.get("snippet") or (getattr(msg, "content", "") or "")[:200],
                "ref": getattr(msg, "session_key", ""),
                "ts": str(getattr(msg, "timestamp", "") or ""),
            }
        )
    return rows, "ok"


async def _telegram(query: str, limit: int) -> tuple[list[Row], str]:
    def _search() -> list[dict]:
        from navig.store.telegram_catalog import get_telegram_catalog

        return get_telegram_catalog().search(query, limit=limit)

    hits = await asyncio.to_thread(_search)
    return [
        {
            "source": "telegram",
            "title": f"{h.get('ref_kind', 'message')} in chat {h.get('chat_id', '')}",
            "snippet": h.get("snippet", ""),
            "ref": f"telegram:{h.get('ref_kind', '')}:{h.get('ref_id', '')}",
        }
        for h in hits
    ], "ok"


async def _recall(query: str, limit: int) -> tuple[list[Row], str]:
    """Navig Recall's typed records (decisions, plans, findings… from past AI sessions).

    Results reach the model, so the read goes through ``recall.query.search`` with
    ``for_model=True`` — the same privacy filter the MCP recall tools apply. Records
    backed by a chat shared below ``agent`` level are withheld, never surfaced here.
    """
    try:
        from navig.recall import query as recall_query
        from navig.recall.paths import index_db
    except ImportError:
        return [], "not installed"
    if not index_db().exists():
        return [], "not enabled (navig recall enable)"
    found = await asyncio.to_thread(lambda: recall_query.search(query, limit=limit, for_model=True))
    return [
        {
            "source": f"recall:{h.get('type', 'record')}",
            "title": h.get("title", ""),
            "snippet": h.get("snip", "") or "",
            "ref": f"recall:{h.get('id', '')}",
            "ts": h.get("updated"),
        }
        for h in found["records"]
    ], "ok"


async def _web(query: str, limit: int) -> tuple[list[Row], str]:
    from navig.tools.web import web_search

    res = await asyncio.to_thread(web_search, query, limit)
    if not res.success:
        return [], f"error: {res.error or 'search failed'}"
    return [
        {"source": "web", "title": getattr(r, "title", ""), "snippet": getattr(r, "snippet", ""), "ref": getattr(r, "url", "")}
        for r in res.results
    ], f"ok ({res.provider})"


# ── planning + fan-out ───────────────────────────────────────────────────────


def plan_sources(include_web: bool) -> tuple[dict[str, Source], dict[str, str]]:
    """Which sources run this call, and why the others don't (skipped → reason)."""
    from navig.agent.conv.sources import source_enabled, web_search_mode
    from navig.memory.controls import memory_off_reason

    run: dict[str, Source] = {}
    skipped: dict[str, str] = {}
    if source_enabled("connectors"):
        run["connectors"] = _connectors
    else:
        skipped["connectors"] = "off in Personalization"
    if source_enabled("library"):
        run["wiki"] = _wiki
        run["cabinet"] = _cabinet
        if (why := memory_off_reason()) is None:
            run["past-chats"] = _past_chats
            run["telegram"] = _telegram
            run["recall"] = _recall
        else:
            for name in ("past-chats", "telegram", "recall"):
                skipped[name] = "memory off" if "incognito" not in why else "incognito chat"
    else:
        for name in ("wiki", "cabinet", "past-chats", "telegram", "recall"):
            skipped[name] = "library off in Personalization"
    if include_web:
        if web_search_mode() == "off":
            skipped["web"] = "web search off in Personalization"
        else:
            run["web"] = _web
    return run, skipped


async def run_search(query: str, include_web: bool = False, limit: int = _PER_SOURCE_LIMIT) -> dict[str, Any]:
    run, skipped = plan_sources(include_web)

    async def _guarded(name: str, fn: Source) -> tuple[str, list[Row], str]:
        try:
            rows, status = await asyncio.wait_for(fn(query, limit), timeout=SOURCE_TIMEOUT_S)
            return name, rows[:limit], status
        except asyncio.TimeoutError:
            return name, [], f"timeout after {SOURCE_TIMEOUT_S:.0f}s"
        except Exception as exc:  # noqa: BLE001 — one source never fails the search
            logger.debug("search_all source %s failed: %s", name, exc)
            return name, [], f"error: {type(exc).__name__}"

    done = await asyncio.gather(*(_guarded(n, f) for n, f in run.items()))
    results: list[Row] = []
    sources: dict[str, str] = {}
    for name, rows, status in done:
        results.extend(rows)
        sources[name] = f"{status} · {len(rows)} hit(s)" if status.startswith("ok") else status
    for name, reason in skipped.items():
        sources[name] = f"skipped: {reason}"
    return {"query": query, "results": results[:_MAX_TOTAL], "count": min(len(results), _MAX_TOTAL), "sources": sources}


class SearchAllTool(BaseTool):
    """Search the user's own sources (and optionally the web) in one call."""

    name = "search_all"
    description = (
        "Search everything the user owns in one call: connected accounts (email, calendar, "
        "drive, notes…), their wiki and Cabinet documents, past chats and Telegram "
        "transcripts — all on this machine — plus the web when include_web is true. Use it "
        "for 'find …', 'where did I …', 'what did X send me about …'. The `sources` field "
        "says which sources answered, which were skipped (and why) and which failed."
    )
    owner_only = False
    parameters = [
        {"name": "query", "type": "string", "description": "What to look for", "required": True},
        {
            "name": "include_web",
            "type": "boolean",
            "description": "Also search the web (default false)",
            "required": False,
        },
        {
            "name": "limit",
            "type": "integer",
            "description": f"Max results per source (default {_PER_SOURCE_LIMIT}, max 10)",
            "required": False,
        },
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        t0 = time.monotonic()
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(name=self.name, success=False, error="'query' arg is required")
        try:
            limit = max(1, min(10, int(args.get("limit") or _PER_SOURCE_LIMIT)))
        except (TypeError, ValueError):
            limit = _PER_SOURCE_LIMIT
        from navig.core.coerce import coerce_bool

        include_web = coerce_bool(args.get("include_web"), default=False)
        try:
            output = await run_search(query, include_web=include_web, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                name=self.name, success=False, error=f"search_all failed: {exc}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        return ToolResult(name=self.name, success=True, output=output, elapsed_ms=(time.monotonic() - t0) * 1000)
