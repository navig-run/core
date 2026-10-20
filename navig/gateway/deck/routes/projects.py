"""Deck API — the project hub.

    GET   /api/deck/projects                          list (pinned first, then recent)
    POST  /api/deck/projects/add          {path, scaffold?}      register a folder
    POST  /api/deck/projects/scan-root    {path, add?}           look one level into a root
    GET   /api/deck/projects/{id}                     full detail
    PATCH /api/deck/projects/{id}/prefs   {pinned?, color?, tags?, …}
    GET   /api/deck/projects/{id}/docs                docs index
    GET   /api/deck/projects/{id}/doc?path=           read one doc (+ fingerprint)
    POST  /api/deck/projects/{id}/doc     {path, content, expect?}
    GET   /api/deck/projects/{id}/todos
    POST  /api/deck/projects/{id}/todos/state   {path, line, state, expect}
    POST  /api/deck/projects/{id}/todos/edit    {path, line, text, expect}
    POST  /api/deck/projects/{id}/todos/add     {path, text, heading?}
    POST  /api/deck/projects/{id}/todos/promote {path, line, expect}   → a board todo
    GET   /api/deck/projects/{id}/context       pack + budget report (cached by fingerprint)
    POST  /api/deck/projects/{id}/context/rebuild
    POST  /api/deck/projects/{id}/context/pin   writes .navig/context.pack.md
    GET   /api/deck/projects/{id}/analyses
    POST  /api/deck/projects/{id}/analyze  {kind: brief|doc|todos, doc?, sources?}  → a PROPOSAL
    POST  /api/deck/projects/{id}/analyses/{aid}/status {status: accepted|rejected}
    GET   /api/deck/projects/{id}/ideas                IDEAS.md by stage + idea docs
    POST  /api/deck/projects/{id}/ideas/{add|edit|move|promote|brief}
    GET   /api/deck/projects/{id}/patterns             the Idea Fabric (builtin + .navig/patterns)
    POST  /api/deck/projects/{id}/patterns/run {pattern, line?, expect?, doc?, sources?, focus?}
    GET   /api/deck/projects/{id}/sessions             Claude Code plans/memory about it + past runs
    GET   /api/deck/projects/{id}/scripts           scripts + each one's latest run
    POST  /api/deck/projects/{id}/scripts/start {key}  LOCAL ONLY — run by key, never a command
    GET   /api/deck/projects/runs[?project=&active=1]
    GET   /api/deck/projects/runs/{run}
    POST  /api/deck/projects/runs/{run}/stop          LOCAL ONLY — kills the whole tree
    GET   /api/deck/projects/runs/{run}/log?cursor=   byte-offset tail
    GET   /api/deck/projects/runs/{run}/stream        SSE tail

Every file path a client sends goes through :func:`navig.projects.docs.resolve_doc`
(the deck is reachable remotely through Lighthouse). Writes are optimistic: a
stale ``expect`` fingerprint is a 409, never a silent overwrite.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)


def _ok(data: Any, status: int = 200) -> web.Response:
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


async def _run(fn: Any, *args: Any, **kw: Any) -> web.Response:
    """Run blocking project work off the loop and map known errors to statuses."""
    from navig.agent.project_analyst import AnalystUnavailableError  # noqa: PLC0415
    from navig.projects.docs import DocError  # noqa: PLC0415
    from navig.projects.model import ProjectNotFound  # noqa: PLC0415
    from navig.projects.runner import RunError  # noqa: PLC0415
    from navig.projects.todos import TodoError  # noqa: PLC0415

    try:
        return _ok(await asyncio.to_thread(fn, *args, **kw))
    except ProjectNotFound as exc:
        return _err(str(exc), 404)
    except AnalystUnavailableError as exc:
        return _err(str(exc), 503)
    except (DocError, TodoError, RunError) as exc:
        return _err(str(exc), exc.status)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        logger.exception("projects route failed")
        return _err(str(exc), 500)


def _root(project_id: str) -> tuple[str, Path]:
    from navig.projects.model import resolve_project  # noqa: PLC0415

    return resolve_project(project_id)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ── list / add / scan ───────────────────────────────────────────────────────


async def handle_projects_list(request: web.Request) -> web.Response:
    from navig.projects.model import list_projects  # noqa: PLC0415

    refresh = request.rel_url.query.get("refresh") in ("1", "true", "yes")
    # The list answers without waiting on git (cached state, else the branch from
    # `.git/HEAD` + `git.pending`); `?git=full` waits for every repo instead.
    fast = request.rel_url.query.get("git") != "full"
    return await _run(list_projects, refresh=refresh, fast=fast)


async def handle_projects_add(request: web.Request) -> web.Response:
    from navig.projects.model import add_project  # noqa: PLC0415

    body = await _body(request)
    path = str(body.get("path") or "").strip()
    if not path:
        return _err("'path' is required")
    return await _run(add_project, path, scaffold=bool(body.get("scaffold")))


async def handle_projects_scan_root(request: web.Request) -> web.Response:
    from navig.projects.model import scan_root  # noqa: PLC0415

    body = await _body(request)
    path = str(body.get("path") or "").strip()
    if not path:
        return _err("'path' is required")
    return await _run(scan_root, path, add=bool(body.get("add")))


# ── one project ─────────────────────────────────────────────────────────────


async def handle_project_get(request: web.Request) -> web.Response:
    from navig.projects.model import get_project  # noqa: PLC0415

    return await _run(get_project, request.match_info["id"])


async def handle_project_prefs(request: web.Request) -> web.Response:
    body = await _body(request)

    def _set() -> dict[str, Any]:
        from navig.projects.model import clear_cache  # noqa: PLC0415
        from navig.projects.store import get_project_store  # noqa: PLC0415

        _pid, root = _root(request.match_info["id"])
        prefs = get_project_store().set_prefs(root, body)
        clear_cache()
        return prefs

    return await _run(_set)


async def handle_project_docs(request: web.Request) -> web.Response:
    def _docs() -> list[dict[str, Any]]:
        from navig.projects.docs import index_docs  # noqa: PLC0415

        return index_docs(_root(request.match_info["id"])[1])

    return await _run(_docs)


async def handle_project_doc_get(request: web.Request) -> web.Response:
    rel = request.rel_url.query.get("path", "")

    def _read() -> dict[str, Any]:
        from navig.projects.docs import read_doc  # noqa: PLC0415

        return read_doc(_root(request.match_info["id"])[1], rel)

    return await _run(_read)


async def handle_project_doc_save(request: web.Request) -> web.Response:
    body = await _body(request)
    content = body.get("content")
    if not isinstance(content, str):
        return _err("content must be a string")
    expect = body.get("expect")

    def _save() -> dict[str, Any]:
        from navig.projects.docs import save_doc  # noqa: PLC0415

        return save_doc(_root(request.match_info["id"])[1], str(body.get("path") or ""), content,
                        expect=str(expect) if expect else None)

    return await _run(_save)


# ── todos ───────────────────────────────────────────────────────────────────


async def handle_project_todos(request: web.Request) -> web.Response:
    def _list() -> dict[str, Any]:
        from navig.projects.todos import extract_todos, summarize  # noqa: PLC0415

        todos = extract_todos(_root(request.match_info["id"])[1])
        return {"todos": todos, "counts": summarize(todos)}

    return await _run(_list)


async def handle_project_todo_state(request: web.Request) -> web.Response:
    body = await _body(request)
    line = _int(body.get("line"))
    if line is None:
        return _err("line must be an integer")

    def _set() -> dict[str, Any]:
        from navig.projects.todos import set_state  # noqa: PLC0415

        return set_state(_root(request.match_info["id"])[1], str(body.get("path") or ""), line,
                         str(body.get("state") or ""), expect=body.get("expect"))

    return await _run(_set)


async def handle_project_todo_edit(request: web.Request) -> web.Response:
    body = await _body(request)
    line = _int(body.get("line"))
    if line is None:
        return _err("line must be an integer")

    def _edit() -> dict[str, Any]:
        from navig.projects.todos import edit_todo  # noqa: PLC0415

        return edit_todo(_root(request.match_info["id"])[1], str(body.get("path") or ""), line,
                         str(body.get("text") or ""), expect=body.get("expect"))

    return await _run(_edit)


async def handle_project_todo_add(request: web.Request) -> web.Response:
    body = await _body(request)

    def _add() -> dict[str, Any]:
        from navig.projects.todos import add_todo  # noqa: PLC0415

        heading = body.get("heading")
        return add_todo(_root(request.match_info["id"])[1], str(body.get("path") or ""),
                        str(body.get("text") or ""), heading=str(heading) if heading else None)

    return await _run(_add)


async def handle_project_todo_promote(request: web.Request) -> web.Response:
    """Turn a doc todo into a board todo, linked back by ``origin_ref``."""
    body = await _body(request)
    line = _int(body.get("line"))
    if line is None:
        return _err("line must be an integer")
    rel = str(body.get("path") or "")

    def _promote() -> dict[str, Any]:
        from navig.projects.docs import resolve_doc  # noqa: PLC0415
        from navig.projects.todos import TodoError, parse_todos  # noqa: PLC0415
        from navig.store.board import get_board_store  # noqa: PLC0415

        pid, root = _root(request.match_info["id"])
        target = resolve_doc(root, rel)
        if not target.is_file():
            raise TodoError("document not found", 404)
        text = target.read_text(encoding="utf-8", errors="replace")
        todo = next((t for t in parse_todos(text, rel) if t["line"] == line), None)
        if todo is None:
            raise TodoError("that line is not a checkbox", 400)
        if body.get("expect") and todo["fingerprint"] != body["expect"]:
            raise TodoError("that todo moved or changed since you loaded it — reload", 409)
        origin_ref = f"{pid}:{rel}:{line + 1}"
        store = get_board_store()
        if store.todo_exists_for_source(origin_ref):
            return {"created": False, "origin_ref": origin_ref}
        card = store.create_todo(
            todo["text"], space=pid, origin="project", origin_ref=origin_ref,
            notes=f"From {rel}" + (f" › {todo['heading']}" if todo.get("heading") else ""),
        )
        return {"created": True, "origin_ref": origin_ref, "card": card}

    return await _run(_promote)


# ── context ─────────────────────────────────────────────────────────────────


def _pack(project_id: str, *, force: bool) -> dict[str, Any]:
    from navig.projects.context_pack import build_pack  # noqa: PLC0415
    from navig.projects.store import get_project_store  # noqa: PLC0415
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    _pid, root = _root(project_id)
    manifest = load_space_manifest(root)
    return build_pack(root, name=manifest.resolved_name or root.name, manifest=manifest.data,
                      store=get_project_store(), force=force)


async def handle_project_context(request: web.Request) -> web.Response:
    return await _run(_pack, request.match_info["id"], force=False)


async def handle_project_context_rebuild(request: web.Request) -> web.Response:
    return await _run(_pack, request.match_info["id"], force=True)


async def handle_project_context_pin(request: web.Request) -> web.Response:
    def _pin() -> dict[str, Any]:
        from navig.projects.context_pack import pin_pack  # noqa: PLC0415

        pack = _pack(request.match_info["id"], force=False)
        return pin_pack(_root(request.match_info["id"])[1], pack)

    return await _run(_pin)


async def handle_project_analyses(request: web.Request) -> web.Response:
    kind = request.rel_url.query.get("kind") or None

    def _list() -> list[dict[str, Any]]:
        from navig.projects.store import get_project_store  # noqa: PLC0415

        return get_project_store().list_analyses(_root(request.match_info["id"])[1], kind=kind)

    return await _run(_list)


# ── AI analysis (proposals only — accepting goes through the doc/todo routes) ──


def _analyze(project_id: str, kind: str, doc: str | None, sources: list[str] | None) -> dict[str, Any]:
    from navig.agent import project_analyst as pa  # noqa: PLC0415
    from navig.projects.docs import DocError, index_docs, read_doc  # noqa: PLC0415
    from navig.projects.store import get_project_store  # noqa: PLC0415
    from navig.projects.todos import extract_todos  # noqa: PLC0415

    _pid, root = _root(project_id)
    pack = _pack(project_id, force=False)
    store = get_project_store()
    if kind == "brief":
        output = pa.brief(pack, pa.doc_freshness(root, index_docs(root)))
        return store.add_analysis(root, "brief", output, fingerprint=pack["fingerprint"])
    if kind == "doc":
        if not doc:
            raise DocError("'doc' is required for a document re-analysis")
        current = read_doc(root, doc)
        if not current["editable"]:
            raise DocError("this document is not UTF-8, so it cannot be rewritten", 422)
        output = pa.reanalyze_doc(pack, current["path"], current["content"])
        # The fingerprint of the doc AS ANALYSED: accepting must save with it as `expect`,
        # so an edit made while the model was thinking is never overwritten.
        output["base_fingerprint"] = current["fingerprint"]
        return store.add_analysis(root, "doc", output, target=current["path"],
                                  fingerprint=pack["fingerprint"])
    if kind == "todos":
        index = index_docs(root)
        if sources:
            chosen = [d for d in index if d["path"] in set(sources)]
        else:
            chosen = sorted((d for d in index if d["group"] in ("briefs", "inbox", "ideas", "tasks")
                             or Path(d["path"]).stem.upper() == "SPEC"),
                            key=lambda d: -float(d.get("mtime") or 0))[:8]
        texts = [(d["path"], read_doc(root, d["path"])["content"]) for d in chosen]
        existing = [t["text"] for t in extract_todos(root, index)]
        output = pa.extract_todos(texts, existing)
        output["sources"] = [d["path"] for d in chosen]
        return store.add_analysis(root, "todos", output, fingerprint=pack["fingerprint"])
    raise DocError("kind must be brief, doc or todos")


async def handle_project_analyze(request: web.Request) -> web.Response:
    body = await _body(request)
    kind = str(body.get("kind") or "")
    doc = str(body["doc"]) if body.get("doc") else None
    sources = [str(s) for s in body.get("sources") or []] or None
    return await _run(_analyze, request.match_info["id"], kind, doc, sources)


async def handle_project_analysis_status(request: web.Request) -> web.Response:
    body = await _body(request)
    status = str(body.get("status") or "")

    def _set() -> dict[str, Any]:
        from navig.projects.store import get_project_store  # noqa: PLC0415

        _root(request.match_info["id"])  # 404 for an unknown project
        if not get_project_store().set_analysis_status(request.match_info["aid"], status):
            raise ValueError("unknown analysis")
        return {"id": request.match_info["aid"], "status": status}

    return await _run(_set)


# ── ideas + the Idea Fabric (patterns over project material) ──────────────────


async def handle_project_ideas(request: web.Request) -> web.Response:
    def _list() -> dict[str, Any]:
        from navig.projects.ideas import list_ideas  # noqa: PLC0415

        return list_ideas(_root(request.match_info["id"])[1])

    return await _run(_list)


async def handle_project_idea_op(request: web.Request) -> web.Response:
    """POST /ideas/{op} — add · edit · move · promote · brief (all fingerprint-guarded)."""
    op = request.match_info["op"]
    body = await _body(request)

    def _do() -> dict[str, Any]:
        from navig.projects import ideas  # noqa: PLC0415

        root = _root(request.match_info["id"])[1]
        line = _int(body.get("line"))
        expect = body.get("expect")
        if op == "add":
            notes = [str(n) for n in body.get("notes") or [] if str(n).strip()]
            return ideas.add_idea(root, str(body.get("text") or ""), stage=str(body.get("stage") or "inbox"),
                                  notes=notes)
        if op == "plan":
            return ideas.adopt_plan(root, str(body.get("markdown") or ""), title=str(body.get("title") or ""),
                                    line=line, expect=expect)
        if line is None:
            raise ValueError("line must be an integer")
        if op == "edit":
            return ideas.edit_idea(root, line, str(body.get("text") or ""), expect=expect)
        if op == "move":
            return ideas.move_idea(root, line, str(body.get("stage") or ""), expect=expect)
        if op == "promote":
            heading = body.get("heading")
            return ideas.promote_idea(root, line, str(body.get("into") or ""),
                                      heading=str(heading) if heading else None, expect=expect)
        if op == "brief":
            return ideas.save_brief(root, line, str(body.get("markdown") or ""), expect=expect)
        raise ValueError(f"unknown idea operation: {op}")

    return await _run(_do)


async def handle_project_patterns(request: web.Request) -> web.Response:
    def _list() -> list[dict[str, Any]]:
        from navig.projects.patterns import list_patterns  # noqa: PLC0415

        root = _root(request.match_info["id"])[1]
        return [{k: v for k, v in p.items() if k not in ("body", "path")} for p in list_patterns(root)]

    return await _run(_list)


async def handle_project_sessions(request: web.Request) -> web.Response:
    def _list() -> dict[str, Any]:
        from navig.projects.sessions import find_sessions  # noqa: PLC0415
        from navig.projects.store import get_project_store  # noqa: PLC0415

        root = _root(request.match_info["id"])[1]
        out = find_sessions(root)
        # Earlier fabric runs (brainstorms, harvests…) are sessions too: what was asked,
        # what came back, what was kept.
        out["runs"] = get_project_store().list_analyses(root, kind="pattern", limit=30)
        harvested = {s for r in out["runs"] for s in (r["output"] or {}).get("inputs", [])}
        for s in out["sources"]:
            s["harvested"] = s["id"] in harvested
        return out

    return await _run(_list)


def _run_pattern(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    from navig.agent import project_analyst as pa  # noqa: PLC0415
    from navig.projects import ideas as _ideas  # noqa: PLC0415
    from navig.projects.docs import DocError, read_doc  # noqa: PLC0415
    from navig.projects.patterns import get_pattern  # noqa: PLC0415
    from navig.projects.sessions import read_source  # noqa: PLC0415
    from navig.projects.store import get_project_store  # noqa: PLC0415
    from navig.projects.todos import extract_todos  # noqa: PLC0415

    _pid, root = _root(project_id)
    pattern = get_pattern(str(body.get("pattern") or ""), root)
    pack = _pack(project_id, force=False)
    listed = _ideas.list_ideas(root)
    existing_ideas = [i["text"] for i in listed["ideas"]]
    existing_todos = [t["text"] for t in extract_todos(root) if t["state"] != "done"]
    kw: dict[str, Any] = {"focus": str(body.get("focus") or ""), "existing_ideas": existing_ideas,
                          "existing_todos": existing_todos}
    inputs: list[str] = []
    target: str | None = None
    if pattern["input"] == "idea":
        line = _int(body.get("line"))
        idea = next((i for i in listed["ideas"] if i["line"] == line), None)
        if idea is None or (body.get("expect") and idea["fingerprint"] != body["expect"]):
            raise DocError("that idea moved or changed since you loaded it — reload", 409)
        text = idea["text"] + ("\n" + "\n".join(f"- {n}" for n in idea["notes"]) if idea["notes"] else "")
        if idea.get("brief"):
            folder = listed["path"].rsplit("/", 1)[0]
            try:
                text += "\n\nBRIEF:\n" + read_doc(root, f"{folder}/{idea['brief']}")["content"]
            except DocError:
                pass
        kw.update(input_label="IDEA", input_text=text)
        target = f"idea:{idea['line']}:{idea['fingerprint']}"
        inputs = [target]
    elif pattern["input"] == "doc":
        doc = read_doc(root, str(body.get("doc") or ""))
        kw.update(input_label=f"DOCUMENT {doc['path']}", input_text=doc["content"])
        target = doc["path"]
        inputs = [doc["path"]]
    elif pattern["input"] == "sources":
        chosen = [str(s) for s in body.get("sources") or []][:12]
        if not chosen:
            raise DocError("pick at least one session or document to harvest")
        sources: list[tuple[str, str]] = []
        for sid in chosen:
            if sid.startswith(("plan:", "memory:")):
                try:
                    sources.append(read_source(root, sid))
                except (OSError, PermissionError) as exc:
                    raise DocError(f"{sid}: {exc}", 400) from exc
            else:
                sources.append((sid, read_doc(root, sid)["content"]))
        kw["sources"] = sources
        inputs = chosen
    output = pa.run_pattern(pattern, pack, **kw)
    output.update(pattern=pattern["id"], title=pattern["title"], inputs=inputs, focus=kw["focus"])
    return get_project_store().add_analysis(root, "pattern", output, target=target,
                                            fingerprint=pack["fingerprint"])


async def handle_project_pattern_run(request: web.Request) -> web.Response:
    body = await _body(request)
    return await _run(_run_pattern, request.match_info["id"], body)


# ── scripts & runs ──────────────────────────────────────────────────────────
# Starting and stopping processes is LOCAL ONLY (the operator's decision: the OS app on
# this machine, never the Mini App over Lighthouse). Status and logs are readable remotely.


def _local_only(request: web.Request) -> web.Response | None:
    from navig.gateway.deck.auth import is_local_request  # noqa: PLC0415

    if is_local_request(request):
        return None
    return _err("scripts can only be started or stopped from this computer (NAVIG OS or the CLI)", 403)


async def handle_project_scripts(request: web.Request) -> web.Response:
    def _scripts() -> dict[str, Any]:
        from navig.projects.profile import build_profile  # noqa: PLC0415
        from navig.projects.runner import get_runner  # noqa: PLC0415

        _pid, root = _root(request.match_info["id"])
        profile = build_profile(root)
        runs = get_runner().list_runs(root=root)
        latest: dict[str, dict[str, Any]] = {}
        for run in runs:  # newest first
            latest.setdefault(run["script_key"], run)
        # `last_run`, not `run`: a script already has a `run` field — the command line
        # it is started with ("pnpm run dev") — and overwriting it lost that.
        scripts = [{**s, "last_run": latest.get(s["key"])} for s in profile.get("scripts", [])]
        return {"scripts": scripts, "packages": profile.get("packages", []),
                "package_manager": profile.get("package_manager"), "can_run": True}

    return await _run(_scripts)


async def handle_project_script_start(request: web.Request) -> web.Response:
    denied = _local_only(request)
    if denied is not None:
        return denied
    body = await _body(request)
    key = str(body.get("key") or "").strip()
    if not key:
        return _err("'key' is required")

    def _start() -> dict[str, Any]:
        from navig.projects.runner import get_runner  # noqa: PLC0415

        pid, root = _root(request.match_info["id"])
        return get_runner().start(pid, root, key)

    return await _run(_start)


async def handle_runs_list(request: web.Request) -> web.Response:
    project = request.rel_url.query.get("project") or None
    active = request.rel_url.query.get("active") in ("1", "true")

    def _list() -> list[dict[str, Any]]:
        from navig.projects.runner import get_runner  # noqa: PLC0415

        root = _root(project)[1] if project else None
        return get_runner().list_runs(root=root, include_finished=not active)

    return await _run(_list)


async def handle_run_get(request: web.Request) -> web.Response:
    from navig.projects.runner import get_runner  # noqa: PLC0415

    return await _run(get_runner().get, request.match_info["run"])


async def handle_run_stop(request: web.Request) -> web.Response:
    denied = _local_only(request)
    if denied is not None:
        return denied
    from navig.projects.runner import get_runner  # noqa: PLC0415

    return await _run(get_runner().stop, request.match_info["run"])


async def handle_run_log(request: web.Request) -> web.Response:
    from navig.projects.runner import get_runner  # noqa: PLC0415

    q = request.rel_url.query
    cursor = int(q["cursor"]) if q.get("cursor", "").isdigit() else None
    max_bytes = int(q["max"]) if q.get("max", "").isdigit() else 64 * 1024
    return await _run(get_runner().tail, request.match_info["run"], cursor, max_bytes)


async def handle_run_stream(request: web.Request) -> web.StreamResponse:
    """SSE: ``{"type":"out","text","cursor"}`` / ``{"type":"status","state"}`` / ``{"type":"exit","code"}``."""
    import json  # noqa: PLC0415

    from navig.projects.runner import RunError, get_runner  # noqa: PLC0415

    run_id = request.match_info["run"]
    runner = get_runner()
    try:
        first = await asyncio.to_thread(runner.tail, run_id, None)
    except RunError as exc:
        return _err(str(exc), exc.status)
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                       "X-Accel-Buffering": "no"})
    await resp.prepare(request)

    async def send(obj: dict[str, Any]) -> bool:
        try:
            await resp.write(f"data: {json.dumps(obj)}\n\n".encode())
            return True
        except Exception:  # noqa: BLE001 — the client went away
            return False

    chunk, state = first, first["state"]
    deadline = asyncio.get_running_loop().time() + 30 * 60  # the client reconnects with its cursor
    if not await send({"type": "status", "state": state}):
        return resp
    while True:
        if chunk["text"] and not await send({"type": "out", "text": chunk["text"], "cursor": chunk["cursor"]}):
            return resp
        if chunk["state"] != state:
            state = chunk["state"]
            if not await send({"type": "status", "state": state}):
                return resp
        if state not in ("starting", "running"):
            await send({"type": "exit", "code": chunk.get("exit_code"), "state": state})
            return resp
        if asyncio.get_running_loop().time() > deadline:
            return resp
        await asyncio.sleep(0.5)
        try:
            chunk = await asyncio.to_thread(runner.tail, run_id, chunk["cursor"])
        except RunError:
            return resp


def register(app: web.Application) -> None:
    """Mount the project routes. Static segments before ``{id}``."""
    r = app.router
    r.add_get("/api/deck/projects", handle_projects_list)
    r.add_post("/api/deck/projects/add", handle_projects_add)
    r.add_post("/api/deck/projects/scan-root", handle_projects_scan_root)
    r.add_get("/api/deck/projects/runs", handle_runs_list)
    r.add_get("/api/deck/projects/runs/{run}", handle_run_get)
    r.add_post("/api/deck/projects/runs/{run}/stop", handle_run_stop)
    r.add_get("/api/deck/projects/runs/{run}/log", handle_run_log)
    r.add_get("/api/deck/projects/runs/{run}/stream", handle_run_stream)
    r.add_get("/api/deck/projects/{id}", handle_project_get)
    r.add_patch("/api/deck/projects/{id}/prefs", handle_project_prefs)
    r.add_get("/api/deck/projects/{id}/docs", handle_project_docs)
    r.add_get("/api/deck/projects/{id}/doc", handle_project_doc_get)
    r.add_post("/api/deck/projects/{id}/doc", handle_project_doc_save)
    r.add_get("/api/deck/projects/{id}/todos", handle_project_todos)
    r.add_post("/api/deck/projects/{id}/todos/state", handle_project_todo_state)
    r.add_post("/api/deck/projects/{id}/todos/edit", handle_project_todo_edit)
    r.add_post("/api/deck/projects/{id}/todos/add", handle_project_todo_add)
    r.add_post("/api/deck/projects/{id}/todos/promote", handle_project_todo_promote)
    r.add_get("/api/deck/projects/{id}/context", handle_project_context)
    r.add_post("/api/deck/projects/{id}/context/rebuild", handle_project_context_rebuild)
    r.add_post("/api/deck/projects/{id}/context/pin", handle_project_context_pin)
    r.add_get("/api/deck/projects/{id}/analyses", handle_project_analyses)
    r.add_post("/api/deck/projects/{id}/analyze", handle_project_analyze)
    r.add_post("/api/deck/projects/{id}/analyses/{aid}/status", handle_project_analysis_status)
    r.add_get("/api/deck/projects/{id}/ideas", handle_project_ideas)
    r.add_post("/api/deck/projects/{id}/ideas/{op}", handle_project_idea_op)
    r.add_get("/api/deck/projects/{id}/patterns", handle_project_patterns)
    r.add_post("/api/deck/projects/{id}/patterns/run", handle_project_pattern_run)
    r.add_get("/api/deck/projects/{id}/sessions", handle_project_sessions)
    r.add_get("/api/deck/projects/{id}/scripts", handle_project_scripts)
    r.add_post("/api/deck/projects/{id}/scripts/start", handle_project_script_start)
