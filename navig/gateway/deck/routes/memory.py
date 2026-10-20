"""Deck/gateway handlers for memory curation (propose → approve → export/import).

These power both the proprietary Deck SPA panel (mounted under ``/api/deck/memory/*``)
and the built-in, dependency-free review page served at ``/memory/review`` (which
calls the gateway-level ``/api/memory/*`` mirror). One handler set, two mount points.

All handlers operate on the shared KeyFactStore (the same db the agent and MCP tools
use). Pending facts (``approved IS NULL``) are proposals; approving moves them into the
set retrieval may inject. Nothing here throws into the request path — failures return
a JSON error with 200/4xx.
"""

from __future__ import annotations

import asyncio
import logging

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None

logger = logging.getLogger(__name__)

# Upper bound for ?limit= on the pending list — an unclamped limit let a caller pull the
# whole store in one request (the deck API is reachable remotely via Lighthouse).
_MAX_PENDING_LIMIT = 500


def _store():
    from navig.memory.key_facts import get_key_fact_store

    return get_key_fact_store()


def _fact_json(f) -> dict:
    return {
        "id": f.id,
        "content": f.content,
        "category": f.category,
        "confidence": f.confidence,
        "tags": f.tags,
        "created_at": f.created_at,
        "updated_at": f.updated_at,
        "source_platform": f.source_platform,
        "pinned": bool(getattr(f, "pinned", False)),
        "redacted": bool((f.metadata or {}).get("redacted")),
    }


# Upper bound for ?limit= on the saved-memories list (same reasoning as pending).
_MAX_LIST_LIMIT = 200
_MAX_FACT_CHARS = 2_200  # matches the agent memory_write cap


def _int_query(request, name: str, default: int) -> int:
    try:
        return int(request.query.get(name, str(default)))
    except (TypeError, ValueError):
        return default


async def handle_memory_list(request: "web.Request") -> "web.Response":
    """GET /api/deck/memory/facts?q=&limit=&offset= — saved (approved) memories."""
    q = (request.query.get("q") or "").strip()[:200]
    limit = max(1, min(_MAX_LIST_LIMIT, _int_query(request, "limit", 50)))
    offset = max(0, _int_query(request, "offset", 0))
    facts, total = await asyncio.to_thread(
        lambda: _store().list_saved(query=q, limit=limit, offset=offset)
    )
    return web.json_response(
        {"facts": [_fact_json(f) for f in facts], "count": len(facts), "total": total}
    )


async def handle_memory_update(request: "web.Request") -> "web.Response":
    """PATCH /api/deck/memory/facts/{fact_id} — body ``{content?, pinned?}``.

    Content goes through the same secrets filter as every write: a secret value is
    replaced (``redacted: true`` in the reply); a body that is only a secret is a 422.
    """
    from navig.memory.fact_redaction import SensitiveContentError

    fact_id = request.match_info.get("fact_id", "")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid JSON body"}, status=400)
    if not isinstance(body, dict) or not ({"content", "pinned"} & body.keys()):
        return web.json_response({"error": "send content and/or pinned"}, status=400)

    content = body.get("content")
    if content is not None:
        if not isinstance(content, str) or not content.strip():
            return web.json_response({"error": "content must be non-empty text"}, status=400)
        if len(content) > _MAX_FACT_CHARS:
            return web.json_response(
                {"error": f"content too long (max {_MAX_FACT_CHARS} chars)"}, status=400
            )
    pinned = body.get("pinned")
    if pinned is not None and not isinstance(pinned, bool):
        return web.json_response({"error": "pinned must be true or false"}, status=400)

    def _apply():
        store = _store()
        fact = store.get(fact_id)
        if fact is None or not fact.is_active:
            return None
        if content is not None:
            fact = store.update_content(fact_id, content.strip())
        if pinned is not None:
            store.set_pinned(fact_id, pinned)
        return store.get(fact_id)

    try:
        fact = await asyncio.to_thread(_apply)
    except SensitiveContentError:
        return web.json_response(
            {"error": "That is only a secret — keep it in the vault, not in memory."},
            status=422,
        )
    if fact is None:
        return web.json_response({"error": "no such memory"}, status=404)
    return web.json_response({"fact": _fact_json(fact)})


async def handle_memory_delete(request: "web.Request") -> "web.Response":
    """DELETE /api/deck/memory/facts/{fact_id} — forget a memory (soft delete)."""
    fact_id = request.match_info.get("fact_id", "")
    ok = await asyncio.to_thread(lambda: _store().soft_delete(fact_id))
    return web.json_response({"ok": ok}, status=200 if ok else 404)


async def handle_memory_summary(request: "web.Request") -> "web.Response":
    """GET → the cached profile summary (+ ``stale``); POST → (re)generate it."""
    from navig.agent.memory_summary import generate_summary, read_summary

    if request.method == "GET":
        data = await asyncio.to_thread(lambda: read_summary(_store()))
        return web.json_response(data)
    try:
        data = await generate_summary(_store(), force=True)
    except Exception as exc:  # noqa: BLE001 — report, never present a stale cache as fresh
        logger.warning("memory summary generation failed: %s", exc)
        return web.json_response({"error": f"Couldn't write the summary: {exc}"}, status=502)
    return web.json_response(data)


async def handle_memory_pending(request: "web.Request") -> "web.Response":
    try:
        limit = int(request.query.get("limit", "200"))
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(_MAX_PENDING_LIMIT, limit))
    # KeyFactStore is synchronous SQLite — every call here runs off the event loop so a big
    # store (or a slow disk) can't stall the gateway. Safe: the store keeps a PER-THREAD
    # connection (threading.local, check_same_thread=False) and serializes writes behind its
    # own _write_lock. `_store()` is resolved INSIDE the thread because it lazily opens the
    # DB / creates the schema on first use.
    facts = await asyncio.to_thread(lambda: _store().get_pending(limit=limit))
    return web.json_response({"facts": [_fact_json(f) for f in facts], "count": len(facts)})


async def handle_memory_approve(request: "web.Request") -> "web.Response":
    fact_id = request.match_info.get("fact_id", "")
    if fact_id == "all":
        approved = await asyncio.to_thread(lambda: _store().approve_all_pending())
        return web.json_response({"approved": approved})
    ok = await asyncio.to_thread(lambda: _store().approve(fact_id))
    return web.json_response({"ok": ok}, status=200 if ok else 404)


async def handle_memory_reject(request: "web.Request") -> "web.Response":
    fact_id = request.match_info.get("fact_id", "")
    reason = ""
    try:
        body = await request.json()
        reason = (body or {}).get("reason", "")
    except Exception:  # noqa: BLE001
        pass
    ok = await asyncio.to_thread(lambda: _store().reject(fact_id, reason or None))
    return web.json_response({"ok": ok}, status=200 if ok else 404)


async def handle_memory_export(request: "web.Request") -> "web.Response":
    fmt = (request.query.get("format") or "json").lower()
    if fmt in ("md", "markdown"):
        text = await asyncio.to_thread(lambda: _store().export_markdown())
        return web.Response(text=text, content_type="text/markdown")
    include_pending = request.query.get("all", "").lower() in ("1", "true", "yes")
    text = await asyncio.to_thread(
        lambda: _store().export_json(approved_only=not include_pending)
    )
    return web.Response(text=text, content_type="application/json")


async def handle_memory_import(request: "web.Request") -> "web.Response":
    try:
        text = await request.text()
    except Exception:  # noqa: BLE001
        text = ""
    try:
        # The heaviest path: a bulk import loops dup-lookup + upsert + FTS insert per fact.
        added, merged = await asyncio.to_thread(lambda: _store().import_json(text))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"added": added, "merged": merged})


# ── Built-in no-SPA review page (gateway-level, localhost) ───────────────────

_REVIEW_HTML = """<!doctype html><meta charset="utf-8">
<title>NAVIG — Memory Review</title>
<style>
 body{font:14px/1.5 system-ui,sans-serif;max-width:760px;margin:32px auto;padding:0 20px;color:#1a1a1a}
 h1{font-size:20px;margin:0 0 4px}.sub{color:#666;margin:0 0 20px}
 .card{border:1px solid #e3e3e3;border-radius:8px;padding:12px 14px;margin:10px 0;display:flex;
   gap:10px;align-items:flex-start;justify-content:space-between}
 .cat{font-size:11px;text-transform:uppercase;color:#0066cc;letter-spacing:.04em}
 .content{margin:2px 0}button{font:inherit;border:1px solid #ccc;background:#fafafa;border-radius:6px;
   padding:5px 10px;cursor:pointer}button:hover{background:#f0f0f0}
 .ok{color:#0a7d28;border-color:#9bd3a8}.no{color:#b00020;border-color:#e3a0a8}
 .empty{color:#888;padding:24px 0}.bar{margin:16px 0;display:flex;gap:8px}
 a{color:#0066cc;text-decoration:none}
</style>
<h1>Memory Review</h1>
<p class="sub">The agent proposes; you decide. Approved facts become part of what it knows about you.</p>
<div class="bar">
 <button onclick="approveAll()">Approve all</button>
 <a href="/api/memory/facts/export?format=md" download="navig-memory.md"><button>Export (Markdown)</button></a>
 <a href="/api/memory/facts/export" download="navig-memory.json"><button>Export (JSON)</button></a>
</div>
<div id="list"><p class="empty">Loading…</p></div>
<script>
async function load(){
 const r = await fetch('/api/memory/facts/pending'); const d = await r.json();
 const el = document.getElementById('list');
 if(!d.facts || !d.facts.length){el.innerHTML='<p class="empty">No pending proposals. \\u2728</p>';return;}
 // EVERY interpolated field is escaped — content was, but category/id were not. Both are
 // server-constrained today (category is coerced to a VALID_CATEGORIES member on upsert, id is
 // a uuid4), so this was latent rather than exploitable; escaping all of them keeps it that way
 // if a category is ever added or validation loosened.
 el.innerHTML = d.facts.map(f=>`<div class="card" data-id="${escapeHtml(f.id)}">
   <div><div class="cat">${escapeHtml(f.category)}</div><div class="content">${escapeHtml(f.content)}</div></div>
   <div style="white-space:nowrap">
     <button class="ok" data-act="approve">Keep</button>
     <button class="no" data-act="reject">Drop</button>
   </div></div>`).join('');
}
// Null-safe: a fact with a null/missing field used to throw on .replace and blank the whole page.
function escapeHtml(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
// Delegated click — no inline onclick, so no id is ever interpolated into a JS string context.
document.getElementById('list').addEventListener('click',e=>{
 const btn=e.target.closest('button[data-act]'); if(!btn) return;
 const card=btn.closest('.card[data-id]'); if(!card) return;
 act(card.dataset.id,btn.dataset.act);
});
async function act(id,which){
 await fetch(`/api/memory/facts/${encodeURIComponent(id)}/${which}`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
 load();
}
async function approveAll(){await fetch('/api/memory/facts/all/approve',{method:'POST'});load();}
load();
</script>
"""


async def handle_memory_review_page(request: "web.Request") -> "web.Response":
    return web.Response(text=_REVIEW_HTML, content_type="text/html")
