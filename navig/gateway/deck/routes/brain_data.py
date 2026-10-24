"""Deck API — the Brain's data surface for linked clients (memory key facts, recall).

A linked machine holds a per-machine token and the Brain's URL
(``~/.navig-os/brain-link.json``). This file is the other end of that contract: the routes
:class:`navig.brain.client.BrainClient` posts to when an MCP tool named a Brain-owned
capability (``navig.mcp.capability``).

    POST /api/deck/brain/data/health
    POST /api/deck/brain/data/memory/remember     {fact, source, tags}
    POST /api/deck/brain/data/memory/update       {fact_id, new_content}
    POST /api/deck/brain/data/memory/forget       {fact_id}
    POST /api/deck/brain/data/memory/retrieve     {query, limit}
    POST /api/deck/brain/data/memory/stats
    POST /api/deck/brain/data/recall/<op>         {args, scope_widened}
    POST /api/deck/brain/data/runtime/<op>        <that tool's own arguments>
    POST /api/deck/brain/data/goals/<op>          <that tool's own arguments>

Responses are the same envelope the rest of the deck uses — ``{"ok": true, "data": …}`` or
``{"ok": false, "error": …, "code": …}`` — because the client refuses anything else. That
refusal is deliberate: a 200 that is an edge error page, a captive portal or an old build
must read as "the call did not run", never as an empty result.

Two guards, in this order, and both matter:

1. **Who is calling.** Only a brain-link device (validated by
   :func:`navig.gateway.deck.auth._brain_link_device`) or this desktop talking to itself may
   read the bank. A paired phone and a Mini App are the operator too, but they are chat
   surfaces, not data authorities: a wider credential surface than the data needs is how a
   memory store ends up on a phone's screenshot.
2. **Whether this host IS the authority.** If this process is a ``client`` or ``executor``
   (``navig.brain.roles``), it answers 409 ``not_authority`` instead of serving rows it may
   itself have received from somewhere else. Without this check two linked machines could
   bounce a call between them and one of them would answer with a stale copy.

The implementations are the MCP bundles' own local functions (``RAW_TOOLS``), not a second
copy: the Brain is simply the machine that runs them. That is also why the role guard is
load-bearing — on a client those functions would write that client's database.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from navig.brain.lease import SINGLETON_ROLES

logger = logging.getLogger(__name__)

PREFIX = "/api/deck/brain/data"

#: The memory operations a client may invoke, mapped to the MCP tool they run. Anything not
#: in this table is not reachable over the network, which keeps the surface as small as the
#: tool catalogue allows.
_MEMORY_OPS: dict[str, str] = {
    "remember": "memory_key_facts_remember",
    "update": "memory_key_facts_update",
    "forget": "memory_key_facts_forget",
    "retrieve": "memory_key_facts_retrieve",
    "stats": "memory_key_facts_stats",
}

#: Recall tools that may be forwarded as tool calls. ``recall_ingest`` is absent on
#: purpose: it reads the transcripts of the machine it runs on, so forwarding it would
#: ingest the Brain's sessions for a client that asked about its own. It has its own route
#: instead — ``POST {PREFIX}/recall/ingest`` — where the CLIENT captures and the Brain
#: receives rows (<see handle_bank_ingest>).
_RECALL_OPS: dict[str, str] = {
    "search": "recall_search",
    "ask": "recall_ask",
    "get": "recall_get",
    "related": "recall_related",
    "timeline": "recall_timeline",
    "decisions": "recall_decisions",
    "open_questions": "recall_open_questions",
    "forgotten_ideas": "recall_forgotten_ideas",
    "tables": "recall_tables",
    "source_excerpt": "recall_source_excerpt",
}


#: Runtime tools a client may invoke. Missions and receipts describe the fleet this Brain
#: runs, so a linked machine asks here instead of keeping a second, emptier fleet.
_RUNTIME_OPS: dict[str, str] = {
    "list_nodes": "navig_runtime_list_nodes",
    "create_mission": "navig_runtime_create_mission",
    "mission_action": "navig_runtime_mission_action",
    "list_missions": "navig_runtime_list_missions",
    "list_receipts": "navig_runtime_list_receipts",
    "trust_score": "navig_runtime_trust_score",
}

#: The four goal tools out of the agent bundle. Sessions, remediation and service control in
#: that bundle stay on the host; only the operator's plan travels.
_GOALS_OPS: dict[str, str] = {
    "list": "navig_agent_goal_list",
    "add": "navig_agent_goal_add",
    "start": "navig_agent_goal_start",
    "cancel": "navig_agent_goal_cancel",
}


def _ok(data: Any, status: int = 200) -> web.Response:
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(message: str, status: int = 400, *, code: str = "", **extra: Any) -> web.Response:
    body: dict[str, Any] = {"ok": False, "error": message}
    if code:
        body["code"] = code
    body.update(extra)
    return web.json_response(body, status=status)


def _authority_role() -> str:
    """The role this install plays, read fresh: a role change must not wait for a TTL."""
    try:
        from navig.brain.roles import detect_install_context

        return str(detect_install_context().get("role") or "")
    except Exception:  # noqa: BLE001 — unreadable config is not an authority claim
        logger.warning("brain data: install role unreadable", exc_info=True)
        return ""


def _caller_allowed(request: web.Request) -> tuple[bool, str]:
    """Whether this caller may reach Brain-owned data, and why not when it may not."""
    if request.get("deck_brain_device"):
        return True, ""
    try:
        from navig.gateway.deck.auth import trusted_local_desktop

        if trusted_local_desktop(request):
            return True, ""
    except Exception:  # noqa: BLE001 — a guard that cannot run denies
        logger.warning("brain data: local-desktop check failed", exc_info=True)
    return False, "brain data is served to linked machines and the local desktop only"


def _guard(request: web.Request) -> web.Response | None:
    allowed, reason = _caller_allowed(request)
    if not allowed:
        return _err(reason, 403, code="forbidden")
    role = _authority_role()
    if role and role not in SINGLETON_ROLES:
        return _err(
            f"this install is a {role}, not the data authority — its Brain owns these rows",
            409,
            code="not_authority",
            role=role,
        )
    return None


async def _body(request: web.Request) -> dict[str, Any]:
    """The JSON body, or {} — a client that posted nothing still gets a real answer."""
    try:
        raw = await request.json()
    except Exception:  # noqa: BLE001 — empty/garbage body is a 400 at the caller's level
        return {}
    return raw if isinstance(raw, dict) else {}


async def _run(fn: Any, *args: Any) -> web.Response:
    """Run one tool implementation off the loop; failures are answers, not tracebacks."""
    try:
        return _ok(await asyncio.to_thread(fn, *args))
    except ValueError as exc:
        return _err(str(exc), 400, code="invalid")
    except Exception as exc:  # noqa: BLE001
        logger.warning("brain data route failed: %s: %s", type(exc).__name__, exc)
        return _err(f"{type(exc).__name__}: {exc}", 500, code="failed")


def _handler_for(tool: str) -> Any:
    """The local implementation of one MCP tool, or None when the catalogue has no such tool."""
    if tool.startswith("memory_key_facts_"):
        from navig.mcp.tools import memory as memory_tools

        return memory_tools.RAW_TOOLS.get(tool)
    if tool.startswith("recall_"):
        from navig.mcp.tools import recall as recall_tools

        return recall_tools.RAW_TOOLS.get(tool)
    if tool.startswith("navig_runtime_"):
        from navig.mcp.tools import runtime as runtime_tools

        return runtime_tools.RAW_TOOLS.get(tool)
    if tool.startswith("navig_agent_goal_"):
        from navig.mcp.tools import agent as agent_tools

        return agent_tools.RAW_TOOLS.get(tool)
    return None


async def _serve_tool_op(request: web.Request, ops: dict[str, str], label: str) -> web.Response:
    """Shared shape for the families whose body IS the tool's own argument object.

    Memory, runtime and goals all pass arguments through untouched, so the Brain runs the
    very same handler the local path would. Only the recall family needs a wrapper
    (``{args, scope_widened}``) and it keeps its own handler.
    """
    if (denied := _guard(request)) is not None:
        return denied
    op = request.match_info.get("op", "")
    tool = ops.get(op)
    if tool is None:
        return _err(f"unknown {label} operation '{op}'", 404, code="unknown_op")
    fn = _handler_for(tool)
    if fn is None:
        return _err(f"this Brain does not serve {tool}", 501, code="unavailable")
    body = await _body(request)
    # `server` is unused by every handler in these bundles.
    return await _run(fn, None, body)


async def handle_memory_op(request: web.Request) -> web.Response:
    return await _serve_tool_op(request, _MEMORY_OPS, "memory")


async def handle_runtime_op(request: web.Request) -> web.Response:
    return await _serve_tool_op(request, _RUNTIME_OPS, "runtime")


async def handle_goals_op(request: web.Request) -> web.Response:
    return await _serve_tool_op(request, _GOALS_OPS, "goals")


async def handle_recall_op(request: web.Request) -> web.Response:
    if (denied := _guard(request)) is not None:
        return denied
    op = request.match_info.get("op", "")
    tool = _RECALL_OPS.get(op)
    if tool is None:
        return _err(f"unknown recall operation '{op}'", 404, code="unknown_op")
    fn = _handler_for(tool)
    if fn is None:
        return _err(f"this Brain does not serve {tool}", 501, code="unavailable")
    body = await _body(request)
    args = body.get("args")
    if not isinstance(args, dict):
        return _err("args must be an object", 400, code="invalid")
    return await _run(fn, None, args)


async def handle_bank_ingest(request: web.Request) -> web.Response:
    """Accept the post-capture rows a linked machine sends, with their provenance.

    This is the one recall operation that is NOT a proxied tool call: ``recall_ingest`` reads
    the transcripts of the machine it runs on, so serving it here would ingest the Brain's own
    sessions for a client that asked about its own. The client captures locally (its
    transcripts stay its own) and sends the rows; the Brain is the bank, so the rows land
    here, with the source's path, adapter and timing intact.

    Replay is safe and expected: ``upsert_source`` is an upsert and ``add_turns`` dedupes on
    ``(source_id, msg_id)``, so a retry or a re-send adds nothing twice.
    """
    if (denied := _guard(request)) is not None:
        return denied
    body = await _body(request)
    sources = body.get("sources")
    turns = body.get("turns")
    if not isinstance(sources, list) or not sources:
        return _err("sources must be a non-empty list", 400, code="invalid")
    if turns is None:
        turns = {}
    if not isinstance(turns, dict):
        return _err("turns must be an object keyed by source_id", 400, code="invalid")

    def _import() -> dict[str, Any]:
        from navig.recall.model import Turn  # noqa: PLC0415
        from navig.recall.store import get_recall_store  # noqa: PLC0415

        store = get_recall_store()
        accepted = 0
        added = 0
        for src in sources:
            if not isinstance(src, dict) or not str(src.get("source_id") or ""):
                continue
            source_id = str(src["source_id"])
            store.upsert_source(src)
            accepted += 1
            raw = turns.get(source_id) or []
            batch = [
                Turn.from_dict(item)
                for item in raw
                if isinstance(item, dict) and item.get("msg_id")
            ]
            if batch:
                added += len(store.add_turns(source_id, batch))
        return {
            "sources": accepted,
            "turns_added": added,
            "machine": str(body.get("machine") or "unknown"),
        }

    return await _run(_import)


async def handle_health(request: web.Request) -> web.Response:
    """Answer even to an unauthorised caller? No — but answer *about* the refusal.

    A client uses this to tell "wrong token" from "wrong URL" from "not the authority", so
    the status code carries the diagnosis and the body repeats it.
    """
    allowed, reason = _caller_allowed(request)
    if not allowed:
        return _err(reason, 403, code="forbidden")
    role = _authority_role()
    return _ok(
        {
            "authority": role in SINGLETON_ROLES,
            "role": role or "unknown",
            "device": request.get("deck_brain_device") or "local-desktop",
        }
    )


def register(app: web.Application) -> None:
    app.router.add_post(f"{PREFIX}/health", handle_health)
    app.router.add_post(f"{PREFIX}/memory/{{op}}", handle_memory_op)
    # Registered BEFORE the `{op}` route: aiohttp matches in registration order, and the
    # import is a different shape (a bank batch) than a forwarded tool call.
    app.router.add_post(f"{PREFIX}/recall/ingest", handle_bank_ingest)
    app.router.add_post(f"{PREFIX}/recall/{{op}}", handle_recall_op)
    app.router.add_post(f"{PREFIX}/runtime/{{op}}", handle_runtime_op)
    app.router.add_post(f"{PREFIX}/goals/{{op}}", handle_goals_op)
