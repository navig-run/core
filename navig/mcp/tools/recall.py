"""MCP tools over Navig Recall — the cited knowledge bank built from past AI conversations.

Served alone by ``navig mcp serve --transport stdio --only recall`` (what
``navig mcp install-config --client claude-code`` registers), or alongside every other
bundle by a plain ``navig mcp serve``. Handlers read the bank in-process, so no daemon is
needed.

Scope (consent tier 4): a read covers the project the client is working in — resolved
from the server's invocation directory, which a stdio client sets to its own workspace —
plus the ``shared`` bucket. ``project`` names another project; ``all_projects=true``
widens the read to every project and has to be asked for explicitly.

Shared chats below ``agent`` level never leave through here (``for_model=True`` in
:mod:`navig.recall.query`): their turns, excerpts and the records they support are withheld.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

logger = logging.getLogger(__name__)

#: Turns per upload request when a client ships its own capture to the Brain.
TURN_PAGE_SIZE = 500

#: Appended to every read tool's description: the one rule a model must not get wrong.
_AUTHORITY = (
    " Every result is cited (source_id, msg_id, ts, speaker; `owner` is true only for the owner's own words: "
    "speaker user_typed, user_answer or user_plan_verdict). A record's `status` comes with `status_basis`: "
    "only `user` means the owner decided it; `audit` is an imported audit, `assistant` is model output and "
    "`verify` is an automated check. Never present an assistant, agent_report or audit record as the owner's "
    "decision."
)

_SCOPE_PROPS: dict[str, Any] = {
    "project": {
        "type": "string",
        "description": "Project id to read instead of the one the client is working in (shared is always included).",
    },
    "all_projects": {
        "type": "boolean",
        "description": "Read every project, not just the current one plus shared. Ask for it explicitly.",
        "default": False,
    },
}


def _schema(
    props: dict[str, Any], required: list[str] | None = None, *, scoped: bool = True
) -> dict[str, Any]:
    p = {**props, **(_SCOPE_PROPS if scoped else {})}
    return {"type": "object", "properties": p, "required": required or []}


_LIMIT = {"type": "integer", "description": "Maximum results (1-50).", "default": 10}
_RECORD_ID = {"type": "string", "description": "Record id (rk-…), from any other recall tool."}

TOOLS: dict[str, dict[str, Any]] = {
    "recall_search": {
        "description": "Search the owner's past AI conversations (Claude Code, Codex, shared chats, session "
        "audits) for decisions, plans, ideas, tables and findings; falls back to the raw "
        "conversation turns when no record matches. Use `raw=true` to search the turns directly."
        + _AUTHORITY,
        "inputSchema": _schema(
            {
                "query": {
                    "type": "string",
                    "description": 'Words to find; "quoted text" matches a phrase.',
                },
                "type": {
                    "type": "string",
                    "description": "Only this record type (decision, plan, idea, table, "
                    "finding, feature, question, …).",
                },
                "status": {
                    "type": "string",
                    "description": "Only this status (approved, confirmed, rejected, …).",
                },
                "raw": {
                    "type": "boolean",
                    "description": "Search conversation turns, not records.",
                    "default": False,
                },
                "limit": _LIMIT,
            },
            ["query"],
        ),
    },
    "recall_ask": {
        "description": "Has the owner asked this before? Returns earlier questions the owner typed, the reply "
        "that followed each one, and matching decisions." + _AUTHORITY,
        "inputSchema": _schema(
            {
                "question": {"type": "string", "description": "The question, in plain words."},
                "limit": {**_LIMIT, "default": 5},
            },
            ["question"],
        ),
    },
    "recall_get": {
        "description": "One record in full: summary, body, every piece of evidence (verbatim, cited), its "
        "timeline and related records." + _AUTHORITY,
        "inputSchema": _schema(
            {
                "id": _RECORD_ID,
                "evidence_limit": {
                    "type": "integer",
                    "description": "Evidence items to return.",
                    "default": 20,
                },
            },
            ["id"],
            scoped=False,
        ),
    },
    "recall_related": {
        "description": "Records linked to one record (evolves, supersedes, related, contradicts, answers)."
        + _AUTHORITY,
        "inputSchema": _schema({"id": _RECORD_ID}, ["id"], scoped=False),
    },
    "recall_timeline": {
        "description": "The append-only history of one record: when it was created, merged, endorsed or "
        "superseded, and by which kind of evidence." + _AUTHORITY,
        "inputSchema": _schema({"id": _RECORD_ID}, ["id"], scoped=False),
    },
    "recall_decisions": {
        "description": "Decisions, newest first: the owner's answers to structured questions and audited "
        "decisions." + _AUTHORITY,
        "inputSchema": _schema(
            {"status": {"type": "string", "description": "Only this status."}, "limit": _LIMIT}
        ),
    },
    "recall_open_questions": {
        "description": "Questions still unresolved, and recorded conflicts nobody settled."
        + _AUTHORITY,
        "inputSchema": _schema({"limit": _LIMIT}),
    },
    "recall_forgotten_ideas": {
        "description": "Ideas nobody has shown were built (impl not_found or unknown) and that were not "
        "rejected, the longest-untouched first." + _AUTHORITY,
        "inputSchema": _schema({"limit": _LIMIT}),
    },
    "recall_resurrect": {
        "description": "The ideas worth reviving, ranked by a deterministic score with the reason each one "
        "scores (the owner's own words quoted in it, audit recovery rank, quoted passages, staleness). "
        "Rejected and superseded ideas never appear, and nothing here is a model opinion — the same bank "
        "always ranks the same way. Use recall_forgotten_ideas for what was set aside without a revival "
        "score, and recall_get to open one idea in full." + _AUTHORITY,
        "inputSchema": _schema({"limit": _LIMIT}),
    },
    "recall_tables": {
        "description": "Markdown tables from past conversations, verbatim (the body is the table)."
        + _AUTHORITY,
        "inputSchema": _schema({"limit": {**_LIMIT, "default": 5}}),
    },
    "recall_source_excerpt": {
        "description": "The original conversation around one cited message (default 3 turns either side) — "
        "check a citation before relying on it." + _AUTHORITY,
        "inputSchema": _schema(
            {
                "source_id": {
                    "type": "string",
                    "description": "Source id from a citation (e.g. cc:<session>).",
                },
                "msg_id": {"type": "string", "description": "Message id from the same citation."},
                "before": {"type": "integer", "default": 3},
                "after": {"type": "integer", "default": 3},
            },
            ["source_id", "msg_id"],
            scoped=False,
        ),
    },
    "recall_ingest": {
        "description": "Read new or changed conversations into the bank (incremental: unchanged files are "
        "skipped). Refuses unless the owner turned reading on (`navig recall enable`).",
        "inputSchema": _schema(
            {
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "claude_code, codex, session_audit, chats (default: all).",
                },
            },
            scoped=False,
        ),
    },
}

SAFETY: dict[str, str] = dict.fromkeys(TOOLS, "safe")
SAFETY["recall_ingest"] = "moderate"  # reads local transcripts and writes the bank


def register(server: Any) -> None:
    """Register the recall tools (self-sufficient on a bare server)."""
    server.tools.update({name: {"name": name, **spec} for name, spec in TOOLS.items()})
    server._tool_handlers.update({name: _routed(name, fn) for name, fn in RAW_TOOLS.items()})
    if not hasattr(server, "_tool_safety"):
        server._tool_safety = {}
    server._tool_safety.update(SAFETY)


# ── helpers ───────────────────────────────────────────────────


def _err(msg: str) -> dict[str, Any]:
    return {"error": msg, "isError": True}


def _limit(args: dict[str, Any], default: int) -> int:
    try:
        n = int(args.get("limit", default))
    except (TypeError, ValueError):
        n = default
    return max(1, min(50, n))


def _truthy(v: Any) -> bool:
    from navig.core.coerce import coerce_bool  # noqa: PLC0415

    return coerce_bool(v, default=False)


def _scope(args: dict[str, Any]):
    from navig.platform.paths import invocation_cwd  # noqa: PLC0415
    from navig.recall.query import scope_for  # noqa: PLC0415

    return scope_for(
        str(args.get("project") or "") or None,
        all_projects=_truthy(args.get("all_projects")),
        cwd=str(invocation_cwd()),
    )


def _hint(found: bool) -> dict[str, Any]:
    """When nothing matched, say whether the bank is simply empty or not enabled."""
    if found:
        return {}
    from navig.recall import query  # noqa: PLC0415

    try:
        st = query.status()
    except Exception:  # noqa: BLE001 — a hint must never fail the call
        return {}
    if not st["enabled"] and not st["records"]:
        return {
            "hint": "Recall is not enabled on this machine. The owner can turn it on with: navig recall enable"
        }
    if not st["records"]:
        return {"hint": "The recall bank is empty. Fill it with: navig recall ingest"}
    return {
        "hint": "Nothing matched in this scope. Try fewer words, raw=true, or all_projects=true."
    }


def _safe(fn):
    def run(server: Any, args: dict[str, Any]) -> Any:
        try:
            return fn(args or {})
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:  # noqa: BLE001 — a tool error, not a server crash
            logger.warning("recall MCP tool %s failed: %s", fn.__name__, type(exc).__name__)
            return _err(f"recall failed: {type(exc).__name__}: {exc}")

    run.__name__ = fn.__name__
    return run


# ── tools ─────────────────────────────────────────────────────


@_safe
def _tool_search(args: dict[str, Any]) -> dict[str, Any]:
    from navig.recall import query  # noqa: PLC0415

    q = str(args.get("query") or "").strip()
    if not q:
        raise ValueError("query is required")
    scope = _scope(args)
    found = query.search(
        q,
        scope=scope,
        rtype=args.get("type") or None,
        status=args.get("status") or None,
        raw=_truthy(args.get("raw")),
        limit=_limit(args, 10),
        for_model=True,
    )
    return {"scope": scope.to_dict(), **found, **_hint(bool(found["records"] or found["turns"]))}


@_safe
def _tool_ask(args: dict[str, Any]) -> dict[str, Any]:
    from navig.recall import query  # noqa: PLC0415

    q = str(args.get("question") or "").strip()
    if not q:
        raise ValueError("question is required")
    scope = _scope(args)
    found = query.ask(q, scope=scope, limit=_limit(args, 5), answer_chars=2000, for_model=True)
    return {"scope": scope.to_dict(), **found, **_hint(bool(found["asked"] or found["decisions"]))}


def _detail(args: dict[str, Any], evidence_limit: int = 20) -> dict[str, Any]:
    from navig.recall import query  # noqa: PLC0415

    rid = str(args.get("id") or "").strip()
    if not rid:
        raise ValueError("id is required")
    d = query.record_detail(rid, evidence_limit=evidence_limit, for_model=True)
    if d is None:
        raise ValueError(f"no record {rid}")
    return d


@_safe
def _tool_get(args: dict[str, Any]) -> dict[str, Any]:
    try:
        n = max(1, min(200, int(args.get("evidence_limit", 20))))
    except (TypeError, ValueError):
        n = 20
    return _detail(args, n)


@_safe
def _tool_related(args: dict[str, Any]) -> dict[str, Any]:
    d = _detail(args, 0)
    return {"id": d["record"]["id"], "related": d["related"]}


@_safe
def _tool_timeline(args: dict[str, Any]) -> dict[str, Any]:
    d = _detail(args, 0)
    r = d["record"]
    return {
        "id": r["id"],
        "title": r["title"],
        "status": r["status"],
        "status_basis": r["status_basis"],
        "events": d["events"],
    }


def _view_tool(name: str, *, with_body: bool = False):
    @_safe
    def _tool_view(args: dict[str, Any]) -> dict[str, Any]:
        from navig.recall import query  # noqa: PLC0415

        scope = _scope(args)
        recs = query.view(
            name,
            scope=scope,
            status=args.get("status") or None,
            limit=_limit(args, 5 if with_body else 10),
            for_model=True,
        )
        rows = [query.record_row(r, with_body=with_body) for r in recs]
        return {
            "scope": scope.to_dict(),
            "view": name,
            "records": rows,
            "count": len(rows),
            **_hint(bool(rows)),
        }

    _tool_view.__name__ = f"_tool_{name}"
    return _tool_view


@_safe
def _tool_resurrect(args: dict[str, Any]) -> dict[str, Any]:
    from navig.recall import query  # noqa: PLC0415

    scope = _scope(args)
    rows = query.resurrect(limit=_limit(args, 10), scope=scope)
    return {"scope": scope.to_dict(), "ideas": rows, "count": len(rows), **_hint(bool(rows))}


@_safe
def _tool_excerpt(args: dict[str, Any]) -> dict[str, Any]:
    from navig.recall import query  # noqa: PLC0415

    sid, mid = str(args.get("source_id") or "").strip(), str(args.get("msg_id") or "").strip()
    if not sid or not mid:
        raise ValueError("source_id and msg_id are required")

    def _n(key: str) -> int:
        try:
            return max(0, min(20, int(args.get(key, 3))))
        except (TypeError, ValueError):
            return 3

    found = query.excerpt(sid, mid, before=_n("before"), after=_n("after"), for_model=True)
    if found is None:
        raise ValueError(f"no message {mid} in {sid}")
    return found


@_safe
def _tool_ingest(args: dict[str, Any]) -> dict[str, Any]:
    from navig.recall.query import is_enabled  # noqa: PLC0415

    if not is_enabled():
        raise ValueError(
            "Recall reading is off on this machine. The owner turns it on with: navig recall enable"
        )
    from navig.recall.ingest import Ingestor  # noqa: PLC0415
    from navig.recall.sources import ADAPTERS  # noqa: PLC0415

    wanted = [str(s) for s in (args.get("sources") or [])] or list(ADAPTERS)
    bad = [s for s in wanted if s not in ADAPTERS]
    if bad:
        raise ValueError(f"unknown source(s): {', '.join(bad)} (use {', '.join(ADAPTERS)})")
    rep = Ingestor().run(wanted)
    out = rep.to_dict()
    out["ok"] = not rep.errors
    return out


# ── Brain routing ──────────────────────────────────────────────
#
# The recall BANK belongs to the Brain (``navig.mcp.capability``): a client that searches
# its own copy answers with rows the Brain never learned, and the operator sees two
# histories under one deck key. So every read is served by the Brain and every database
# open happens there. Ingestion is different: it reads THIS machine's session transcripts,
# so it stays local in Phase C where provenance travels with the rows — until then a linked
# client is refused rather than allowed to fill a second bank it will never read.

RAW_TOOLS: dict[str, Any] = {
    "recall_search": _tool_search,
    "recall_ask": _tool_ask,
    "recall_get": _tool_get,
    "recall_related": _tool_related,
    "recall_timeline": _tool_timeline,
    "recall_decisions": _view_tool("decisions"),
    "recall_open_questions": _view_tool("open_questions"),
    "recall_forgotten_ideas": _view_tool("forgotten_ideas"),
    "recall_resurrect": _tool_resurrect,
    "recall_tables": _view_tool("tables", with_body=True),
    "recall_source_excerpt": _tool_excerpt,
    "recall_ingest": _tool_ingest,
}


def _brain_args(args: dict[str, Any]) -> dict[str, Any]:
    """The arguments to send to the Brain, with the scope made explicit.

    ``project`` and ``all_projects`` mean the same thing on both hosts, so they travel
    unchanged. A call that names neither was relying on the *client's* current directory —
    a path the Brain cannot resolve — so it is widened to all projects and the answer says
    so, which is a broader result the caller can see, not a quietly different one.
    """
    forwarded = dict(args or {})
    if not str(forwarded.get("project") or "").strip() and not _truthy(
        forwarded.get("all_projects")
    ):
        forwarded["all_projects"] = True
        forwarded["_scope_widened"] = True
    return forwarded


def _ingest_with_upload(client: Any, route: Any) -> dict[str, Any]:
    """Capture this machine's own transcripts, then ship the rows to the Brain.

    The transcripts are local by nature — they are this machine's sessions — so the capture
    stays here and only the resulting rows travel. No extraction runs on the client, so the
    Brain stays the one place that decides what a record means, and re-sending is safe: the
    Brain upserts the source and dedupes turns on ``(source_id, msg_id)``.

    ``upload`` reports what actually crossed: ``sources`` (fully sent), ``turns_sent`` and
    ``turns_added``. A source goes in ``TURN_PAGE_SIZE`` pages, so a bank of any size is a
    bounded series of requests rather than one body, and a failure part-way keeps the local
    capture and still reports the pages that landed.
    """
    from navig.brain.client import BrainUnavailable
    from navig.recall.ingest import Ingestor
    from navig.recall.sources import ADAPTERS
    from navig.recall.store import get_recall_store

    store = get_recall_store()
    before = {s["source_id"]: (s.get("mtime"), s.get("cursor")) for s in store.list_sources()}
    report = Ingestor(store=store).run(list(ADAPTERS))
    out = report.to_dict()
    out["ok"] = not report.errors

    touched = [
        source
        for source in store.list_sources()
        if source["source_id"] not in before
        or (source.get("mtime"), source.get("cursor")) != before[source["source_id"]]
    ]
    machine = socket.gethostname()
    if not touched:
        out["upload"] = {"sources": 0, "turns_sent": 0, "turns_added": 0, "machine": machine}
        out["machine"] = machine
        return out

    upload: dict[str, Any] = {"sources": 0, "turns_sent": 0, "turns_added": 0, "machine": machine}
    try:
        for source in touched:
            sent, added = _upload_source(client, store, source, machine)
            upload["sources"] += 1
            upload["turns_sent"] += sent
            upload["turns_added"] += added
    except BrainUnavailable as exc:
        out["isError"] = True
        out["ok"] = False
        out["captured_local"] = True
        out["upload"] = upload
        out["error"] = (
            "captured on this machine, but the Brain did not accept the upload "
            f"({exc}). {upload['turns_sent']} turn(s) from {upload['sources']} source(s) "
            "reached the Brain; everything else is kept and the next ingest re-sends it."
        )
    else:
        out["upload"] = upload
    return out


def _upload_source(
    client: Any, store: Any, source: dict[str, Any], machine: str
) -> tuple[int, int]:
    """Ship one source in pages; returns ``(turns_sent, turns_added)``.

    A bank is unbounded and a request is not: the relay carries bodies as text and gives a
    request thirty seconds, so one source goes over in ``TURN_PAGE_SIZE`` slices. Sending a
    page twice adds nothing — the Brain dedupes turns on ``(source_id, msg_id)`` — so a
    retry after a failed page is the same call again.
    """
    source_id = str(source["source_id"])
    sent = added = 0
    cursor: int | None = None
    while True:
        turns, cursor = store.turns_page(source_id, after_seq=cursor, limit=TURN_PAGE_SIZE)
        if not turns:
            break
        result = client.bank_ingest(
            [source], {source_id: [turn.to_dict() for turn in turns]}, machine=machine
        )
        sent += len(turns)
        added += int((result or {}).get("turns_added") or 0)
        if len(turns) < TURN_PAGE_SIZE:
            break
    return sent, added


def _routed(name: str, local: Any) -> Any:
    """Route a recall call to the Brain, or explain why it cannot be served."""

    def run(server: Any, args: dict[str, Any]) -> Any:
        from navig.brain.client import BrainUnavailable
        from navig.mcp import capability

        route = capability.resolve(name)
        if route.is_refused:
            return route.refusal
        if route.is_brain and route.client is not None:
            if name == "recall_ingest":
                return _ingest_with_upload(route.client, route)
            try:
                result = route.client.recall_op(name, _brain_args(args))
            except BrainUnavailable as exc:
                return capability.failure_payload(exc, route.capability)
            if (
                isinstance(result, dict)
                and args.get("all_projects") is None
                and (not str(args.get("project") or "").strip())
            ):
                result.setdefault("scope", "all-projects")
                result.setdefault(
                    "scope_note",
                    "served by the Brain; this call named no project, so all projects were searched",
                )
            return result
        return local(server, args)

    run.__name__ = getattr(local, "__name__", name)
    run.__wrapped__ = local  # the Brain executes this, never the wrapper
    return run
