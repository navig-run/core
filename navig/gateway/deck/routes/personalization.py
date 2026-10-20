"""GET/POST /api/deck/personalization — the OS Personalization page's settings.

One route for every switch on the page, so the UI reads and writes a single document:

    {
      "memory":      {"enabled": bool},
      "style":       {"warmth"|"enthusiasm"|"emoji"|"headers_lists"|"visual": "less"|"default"|"more"},
      "web_search":  {"mode": "auto"|"always"|"off"},
      "fast_answers": bool,
      "sources":     {"library": bool, "connectors": bool}
    }

POST takes any subset of that shape. Every key is whitelisted and type-checked; an
unknown key or a bad value is a 400 and NOTHING is written (validate all, then write).
Writes go through ``ConfigManager.set_global`` — the refresh → deep-set → save path —
one leaf at a time, so no sibling setting is ever clobbered. Custom instructions are
not here: they are ``USER.md``, served by ``/api/deck/customize/instructions/USER.md``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None

logger = logging.getLogger(__name__)


def _read() -> dict[str, Any]:
    from navig.agent.conv.sources import (
        fast_answers_enabled,
        source_enabled,
        web_search_mode,
    )
    from navig.agent.conv.style import DIALS, normalize_level
    from navig.config import get_config_manager
    from navig.memory.controls import memory_enabled

    raw_style = get_config_manager().get_live("style", {}) or {}
    if not isinstance(raw_style, dict):
        raw_style = {}
    return {
        "memory": {"enabled": memory_enabled()},
        "style": {d: normalize_level(raw_style.get(d)) for d in DIALS},
        "web_search": {"mode": web_search_mode()},
        "fast_answers": fast_answers_enabled(),
        "sources": {
            "library": source_enabled("library"),
            "connectors": source_enabled("connectors"),
        },
    }


def _plan_writes(body: Any) -> tuple[list[tuple[str, Any]], str | None]:
    """Validate *body*; return ``(writes, error)``. Nothing is written here."""
    from navig.agent.conv.sources import WEB_MODES
    from navig.agent.conv.style import DIALS, LEVELS

    if not isinstance(body, dict) or not body:
        return [], "send a JSON object with at least one setting"
    writes: list[tuple[str, Any]] = []

    def _section(name: str) -> dict | None:
        val = body.get(name)
        return val if isinstance(val, dict) else None

    allowed = {"memory", "style", "web_search", "fast_answers", "sources"}
    if unknown := sorted(set(body) - allowed):
        return [], f"unknown setting(s): {', '.join(unknown)}"

    if "memory" in body:
        sec = _section("memory")
        if sec is None or set(sec) - {"enabled"} or not isinstance(sec.get("enabled"), bool):
            return [], "memory must be {\"enabled\": true|false}"
        writes.append(("memory.enabled", sec["enabled"]))
    if "style" in body:
        sec = _section("style")
        if sec is None:
            return [], "style must be an object"
        for dial, level in sec.items():
            if dial not in DIALS:
                return [], f"unknown style dial: {dial}"
            if level not in LEVELS:
                return [], f"style.{dial} must be one of {', '.join(LEVELS)}"
            writes.append((f"style.{dial}", level))
    if "web_search" in body:
        sec = _section("web_search")
        if sec is None or set(sec) - {"mode"} or sec.get("mode") not in WEB_MODES:
            return [], f"web_search must be {{\"mode\": {'|'.join(WEB_MODES)}}}"
        writes.append(("ai.web_search.mode", sec["mode"]))
    if "fast_answers" in body:
        if not isinstance(body["fast_answers"], bool):
            return [], "fast_answers must be true or false"
        writes.append(("ai.fast_answers", body["fast_answers"]))
    if "sources" in body:
        sec = _section("sources")
        if sec is None:
            return [], "sources must be an object"
        for name, val in sec.items():
            if name not in ("library", "connectors"):
                return [], f"unknown source: {name}"
            if not isinstance(val, bool):
                return [], f"sources.{name} must be true or false"
            writes.append((f"ai.sources.{name}", val))
    return writes, None


_MAX_INSTRUCTIONS_CHARS = 6_000  # same order as the core agent's USER.md cap
_MAX_QUERY_CHARS = 2_000


def _context(query: str, incognito: bool) -> dict[str, Any]:
    """What another agent (NAVIG OS chat) needs to answer like NAVIG would.

    ``system`` is session-stable (style rules + custom instructions) — send it once per
    conversation and again when ``system_hash`` changes. ``memory`` is per message: the
    approved facts relevant to *query*, empty when memory is off or the chat is
    incognito. Both are plain text the caller can inject as-is.
    """
    import hashlib

    from navig.agent.conv.style import read_style, style_rules
    from navig.memory.controls import memory_enabled

    parts: list[str] = []
    rules = style_rules(read_style())
    if rules:
        parts.append("## Answer style\n" + "\n".join(f"- {r}" for r in rules))
    try:
        from navig.workspace import WorkspaceManager

        instructions = (WorkspaceManager().get_file_content("USER.md") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("USER.md unavailable: %s", exc)
        instructions = ""
    if instructions:
        if len(instructions) > _MAX_INSTRUCTIONS_CHARS:
            instructions = instructions[:_MAX_INSTRUCTIONS_CHARS].rstrip() + " …[truncated]"
        parts.append("## About the user (their custom instructions)\n" + instructions)
    system = "\n\n".join(parts)

    enabled = memory_enabled()
    memory = ""
    if enabled and not incognito and query:
        try:
            from navig.memory.fact_retriever import FactRetriever
            from navig.memory.key_facts import get_key_fact_store

            result = FactRetriever(get_key_fact_store()).retrieve(query=query, max_tokens=400)
            if result and result.formatted:
                memory = f"## What I remember\n{result.formatted}"
        except Exception as exc:  # noqa: BLE001 — recall is best-effort, never a failed turn
            logger.debug("context recall skipped: %s", exc)
    return {
        "system": system,
        "system_hash": hashlib.sha256(system.encode("utf-8")).hexdigest()[:16],
        "memory": memory,
        "memory_enabled": enabled,
    }


async def handle_personalization_context(request: "web.Request") -> "web.Response":
    """GET /api/deck/personalization/context?q=<message>&incognito=0|1"""
    from navig.core.coerce import coerce_bool

    query = (request.query.get("q") or "")[:_MAX_QUERY_CHARS]
    incognito = coerce_bool(request.query.get("incognito"), default=False)
    return web.json_response(await asyncio.to_thread(_context, query, incognito))


async def handle_personalization_get(request: "web.Request") -> "web.Response":
    return web.json_response(await asyncio.to_thread(_read))


async def handle_personalization_post(request: "web.Request") -> "web.Response":
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid JSON body"}, status=400)
    writes, error = _plan_writes(body)
    if error:
        return web.json_response({"error": error}, status=400)

    def _apply() -> dict[str, Any]:
        from navig.config import get_config_manager

        cm = get_config_manager()
        for key, value in writes:
            cm.set_global(key, value)
        return _read()

    try:
        settings = await asyncio.to_thread(_apply)
    except Exception as exc:  # noqa: BLE001
        logger.warning("personalization save failed: %s", exc)
        return web.json_response({"error": f"Couldn't save: {exc}"}, status=500)
    return web.json_response(settings)
