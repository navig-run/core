"""``perplexity_*`` MCP tools — delegate work to the Perplexity desktop app.

The app runs on the operator's own subscription, so work it completes costs the agent no
tokens: this bundle is how an agent spends *the app's* budget instead of its own context.
The handlers call :mod:`navig_perplexity.engine` — the same delegation path as the
``navig perplexity`` CLI — so there is no second implementation to drift. Imported lazily, so
core never hard-depends on the plugin being installed.

Every handler is synchronous and the driver is synchronous — one call, one result, no event
loop left running behind a tool call.
"""

from __future__ import annotations

from typing import Any

_INSTALL_HINT = (
    "The Perplexity desktop app bridge is not installed. "
    "Install it with: pip install navig-perplexity"
)


def _engine():
    """(Request, delegate, errors) from the plugin, or None when it is not installed."""
    try:
        from navig_perplexity import tasks as tasks_mod  # noqa: F401 - imported for its errors
        from navig_perplexity.engine import DelegationError, Request, delegate
        from navig_perplexity.threads import ThreadError
    except ImportError:
        return None
    return Request, delegate, (DelegationError, ThreadError, RuntimeError)


def _missing() -> dict[str, Any]:
    return {"ok": False, "success": False, "status": "failed", "error": _INSTALL_HINT}


def _failed(exc: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "success": False, "status": "failed", "error": str(exc)}
    hint = getattr(exc, "hint", "")
    if hint:
        payload["hint"] = hint
    candidates = getattr(exc, "candidates", None)
    if candidates:
        payload["candidates"] = candidates
    return payload


def _run(args: dict[str, Any], *, default_task: str = "auto") -> dict[str, Any]:
    engine = _engine()
    if engine is None:
        return _missing()
    Request, delegate, errors = engine
    prompt = str(args.get("question") or args.get("brief") or "").strip()
    if not prompt:
        return {"ok": False, "success": False, "status": "failed", "error": "a prompt is required"}
    request = Request(
        prompt=prompt,
        task=str(args.get("task") or default_task),
        model=str(args.get("model") or "auto"),
        reasoning=str(args.get("reasoning") or "auto"),
        mode=str(args.get("mode") or "auto"),
        thread=args.get("thread") or None,
        topic=args.get("topic") or None,
        project=args.get("project") or None,
        new=bool(args.get("new", False)),
        timeout=min(float(args.get("timeout") or 180), 300.0),
        out=args.get("out") or None,
        force=bool(args.get("force", False)),
        full=bool(args.get("full", False)),
        wait=min(float(args.get("wait") or 120), 300.0),
        cross_project=bool(args.get("cross_project", False)),
        attach=[str(p) for p in (args.get("attach") or [])],
        connectors=[str(c) for c in (args.get("connectors") or [])],
        lifecycle=str(args.get("lifecycle") or ""),
        backend=str(args.get("backend") or "auto"),
        media=bool(args.get("media", False)),
    )
    try:
        return delegate(request).as_dict()
    except errors as exc:  # noqa: BLE001 - every failure becomes a tool result
        return _failed(exc)


# ────────────────────────── handlers ──────────────────────────


def _tool_perplexity_ask(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    return _run(args)


def _tool_perplexity_write(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    return _run(args, default_task="article")


def _tool_perplexity_threads(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    try:
        from navig_perplexity.engine import (
            DelegationError,
            forget,
            prune_conversations,
            session_identity,
        )
        from navig_perplexity.threads import Registry, ThreadError
    except ImportError:
        return _missing()

    action = str(args.get("action") or "list").strip().lower()
    registry = Registry()
    try:
        if action == "list":
            return {
                "ok": True,
                "threads": registry.list(
                    project=args.get("project") or None,
                    include_archived=bool(args.get("all", False)),
                    lifecycle=args.get("lifecycle") or None,
                    session=args.get("session") or None,
                    limit=int(args.get("limit") or 20),
                ),
            }
        if action == "sessions":
            return {"ok": True, "current": session_identity(),
                    "sessions": registry.session_summary()}
        if action == "set_lifecycle":
            alias = str(args.get("alias") or "").strip()
            if not alias:
                return {"ok": False, "error": "action 'set_lifecycle' needs an alias"}
            lifecycle = str(args.get("lifecycle") or "").strip()
            return {"ok": True, "thread": registry.set_lifecycle(alias, lifecycle).as_dict()}
        if action == "delete":
            alias = str(args.get("alias") or "").strip()
            if not alias:
                return {"ok": False, "error": "action 'delete' needs an alias"}
            if not bool(args.get("confirm", False)):
                return {
                    "ok": False,
                    "error": "deleting a conversation is destructive: pass confirm=true",
                    "preview": registry.get(alias).as_dict(),
                }
            result = forget([registry.get(alias)], remote=not bool(args.get("registry_only", False)))
            return {"ok": result["success"], **result}
        if action == "prune":
            scope: dict[str, Any] = {}
            if args.get("session"):
                scope["session"] = str(args["session"])
            if args.get("project"):
                scope["project"] = str(args["project"])
            if args.get("older_than_seconds"):
                scope["older_than_s"] = float(args["older_than_seconds"])
            if args.get("title"):
                scope["title_like"] = str(args["title"])
            if not scope:
                scope["session"] = session_identity()
            if args.get("include_persistent"):
                scope["include_persistent"] = True
            if args.get("include_artifacts"):
                scope["include_artifacts"] = True
            if args.get("force"):
                scope["force"] = True
            preview = bool(args.get("dry_run", True)) or not bool(args.get("confirm", False))
            result = prune_conversations(
                remote=not bool(args.get("registry_only", False)),
                dry_run=preview,
                **scope,
            )
            return {"ok": True, **result}
        alias = str(args.get("alias") or "").strip()
        if not alias:
            return {"ok": False, "error": f"action {action!r} needs an alias"}
        if action == "show":
            return {"ok": True, "thread": registry.get(alias).as_dict()}
        if action == "archive":
            return {"ok": True, "thread": registry.archive(alias).as_dict()}
        if action == "resume":
            thread = registry.get(alias)
            return {"ok": True, "thread": registry.resume(alias).as_dict(),
                    "note": "" if thread.remote_id else "this thread has no app id yet"}
        return {
            "ok": False,
            "error": f"unknown action {action!r} "
                     "(list, show, resume, archive, delete, prune, sessions)",
        }
    except ThreadError as exc:
        return _failed(exc)
    except DelegationError as exc:
        return _failed(exc)


def _tool_perplexity_capabilities(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    try:
        from navig_perplexity import capabilities as caps_mod
        from navig_perplexity.driver import Driver
    except ImportError:
        return _missing()
    record = caps_mod.load()
    if bool(args.get("refresh", False)) or not caps_mod.is_fresh(record):
        try:
            with Driver(timeout=60) as driver:
                caps_mod.save(caps_mod.build_from_probe(driver.capability_probe()))
        except Exception as exc:  # noqa: BLE001 - report what could not be probed
            return _failed(exc)
        record = caps_mod.load()
    return {"ok": True, "probed_at": record.get("probed_at"),
            "capabilities": record.get("capabilities") or []}


def _tool_perplexity_models(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    try:
        from navig_perplexity.driver import Driver
    except ImportError:
        return _missing()
    try:
        with Driver(timeout=60) as driver:
            return driver.models()
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)


def _tool_perplexity_model(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    try:
        from navig_perplexity.driver import Driver
    except ImportError:
        return _missing()
    name = str(args.get("name") or "").strip()
    try:
        with Driver(timeout=60) as driver:
            if not name:
                return {"ok": True, "current": driver.models().get("current", "")}
            return driver.set_model(name)
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)


def _tool_perplexity_status(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    try:
        from navig_perplexity import app as appmod
    except ImportError:
        return _missing()
    info = appmod.status()
    try:
        from navig_perplexity import api as api_mod

        info["backend"] = {"api_key_configured": api_mod.available()}
    except ImportError:
        pass
    try:
        from navig_perplexity.driver import Driver

        if info.get("port"):
            with Driver(timeout=30) as driver:
                info["session"] = driver.status()
    except Exception as exc:  # noqa: BLE001 - status reports what it could not read
        info["session_error"] = str(exc)
    return info


# ────────────────────────── registration ──────────────────────────

_THREADS_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "show", "resume", "archive", "delete", "prune", "sessions",
                     "set_lifecycle"],
            "default": "list",
            "description": "What to do with the registry. 'delete'/'prune' need confirm=true.",
        },
        "alias": {"type": "string", "description": "Conversation alias (show/resume/archive/delete)."},
        "project": {"type": "string", "description": "Only conversations for this project."},
        "all": {"type": "boolean", "default": False, "description": "Include archived ones."},
        "limit": {"type": "integer", "default": 20, "description": "How many to return."},
        "session": {"type": "string", "description": "Only conversations left by this agent session."},
        "older_than_seconds": {"type": "number", "description": "Only ones last used longer ago than this."},
        "title": {"type": "string", "description": "Only conversations whose title contains this."},
        "confirm": {"type": "boolean", "default": False,
                    "description": "Required for delete/prune: they remove the app's history."},
        "dry_run": {"type": "boolean", "default": True,
                    "description": "prune previews unless confirm is also true."},
        "registry_only": {"type": "boolean", "default": False,
                          "description": "Forget the row but keep the app's conversation."},
        "lifecycle": {"type": "string", "enum": ["temporary", "persistent", "archived",
                                                 "deletion_pending"],
                      "description": "Filter by lifecycle, or the new state for set_lifecycle."},
        "include_persistent": {"type": "boolean", "default": False,
                               "description": "prune: also consider persistent/archived work."},
        "include_artifacts": {"type": "boolean", "default": False,
                              "description": "prune: also consider conversations that produced files."},
        "force": {"type": "boolean", "default": False,
                  "description": "delete/prune: allow protected (persistent/archived) work."},
    },
    "required": [],
}

_WORK_PROPS = {
    "task": {"type": "string",
             "description": "auto|research|investigate|article|marketing|social|naming|brainstorm|"
                            "analysis|compare|translate|critique|second-opinion|report|rewrite|structured"},
    "model": {"type": "string", "description": "'auto' (routed), or a model label from perplexity_models."},
    "reasoning": {"type": "string", "enum": ["auto", "on", "off"],
                  "description": "Reasoning is satisfied by model choice in this build."},
    "mode": {"type": "string", "enum": ["auto", "search", "research", "computer"],
             "description": "How the app answers: search, research or computer; auto lets it choose."},
    "thread": {"type": "string", "description": "Continue this conversation alias."},
    "topic": {"type": "string", "description": "Subject label: reuse the thread for this topic."},
    "project": {"type": "string", "description": "Project identity (defaults to the repo's name)."},
    "new": {"type": "boolean", "default": False, "description": "Start a fresh conversation."},
    "timeout": {"type": "number", "default": 180, "description": "Seconds to wait (max 300)."},
    "out": {"type": "string", "description": "Save the result to this path."},
    "force": {"type": "boolean", "default": False, "description": "Overwrite an existing --out file."},
    "full": {"type": "boolean", "default": False, "description": "Write brief + content + sources."},
    "wait": {"type": "number", "default": 120, "description": "Seconds to wait for a busy composer."},
    "cross_project": {"type": "boolean", "default": False,
                      "description": "Reuse a thread that belongs to another project."},
    "attach": {"type": "array", "items": {"type": "string"},
               "description": "Local files to attach as context (PDF, doc, image, code)."},
    "connectors": {"type": "array", "items": {"type": "string"},
                   "description": "Already-authorized app connectors to toggle for this request "
                                  "(see perplexity_capabilities); restored afterwards."},
    "backend": {"type": "string", "enum": ["auto", "cdp", "api"],
                "description": "Transport: auto uses the API when a key is configured, otherwise "
                               "the desktop app. api = text+citations only (no modes/connectors/files)."},
    "media": {"type": "boolean", "default": False,
              "description": "Copy the images the answer shows into the conversation's media folder."},
    "lifecycle": {"type": "string", "enum": ["temporary", "persistent"],
                  "description": "temporary = disposable scratch work (cleanup may delete it); "
                                 "persistent = project work (protected). Defaults from the task."},
}


def register(server: Any) -> None:
    """Register the perplexity_* tools."""
    server.tools.update({
        "perplexity_ask": {
            "name": "perplexity_ask",
            "description": (
                "Delegate intellectual work to the Perplexity desktop app and return the finished "
                "result as structured content (text + citation URLs + thread identity). Research, "
                "investigation, analysis, comparisons, critique, translation, brainstorming. The "
                "model and reasoning mode are routed from the task; the conversation is reused for "
                "the same project/topic and can be continued later with `thread`."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The work to delegate. Markdown and multi-line briefs are fine."},
                    **_WORK_PROPS,
                },
                "required": ["question"],
            },
        },
        "perplexity_write": {
            "name": "perplexity_write",
            "description": (
                "Write publication-shaped content with the Perplexity app: an article, launch or "
                "marketing copy, a social post, a report, a translation, a rewrite. Same engine and "
                "result shape as perplexity_ask, with an editorial default task."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "brief": {"type": "string", "description": "What to write, with audience and format."},
                    **_WORK_PROPS,
                },
                "required": ["brief"],
            },
        },
        "perplexity_capabilities": {
            "name": "perplexity_capabilities",
            "description": (
                "Reports what this Perplexity build and account actually expose — modes (search, "
                "deep research, learn step by step, model council, computer), file upload, "
                "connectors, image and video generation — one row per capability with the UI "
                "evidence it came from and a status of verified / gated / unsupported / unverified. "
                "Returns the cached record unless refresh is true. Call it before promising a "
                "multimodal or connector workflow, or when perplexity_ask rejects a mode or "
                "connector; use perplexity_models for the model picker and perplexity_ask to "
                "delegate the work itself instead of guessing at capabilities."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "refresh": {
                        "type": "boolean",
                        "default": False,
                        "description": "Probe the running app again instead of returning the cache.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        "perplexity_threads": {
            "name": "perplexity_threads",
            "description": (
                "The persistent conversation registry: list delegated conversations, show one, "
                "resume it in the app, archive it, or clean up. A conversation survives an agent "
                "restart — continue it with perplexity_ask thread=<alias>. Cleanup: action=prune "
                "deletes this session's conversations from the app's history too (confirm=true), "
                "action=sessions shows which sessions left conversations behind."
            ),
            "inputSchema": _THREADS_SCHEMA,
        },
        "perplexity_models": {
            "name": "perplexity_models",
            "description": (
                "List the models this account can select, with the routing traits (thinking, "
                "writing, reasoning, research, speed) and the gated tiers."
            ),
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        "perplexity_model": {
            "name": "perplexity_model",
            "description": "Read or set the app's model explicitly (bypassing routing). Omit name to read it.",
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Model label to select."}},
                "required": [],
            },
        },
        "perplexity_status": {
            "name": "perplexity_status",
            "description": "Check whether the app is attachable, which conversation is open and which model is set.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
    })

    server._tool_handlers.update({
        "perplexity_ask": _tool_perplexity_ask,
        "perplexity_write": _tool_perplexity_write,
        "perplexity_threads": _tool_perplexity_threads,
        "perplexity_capabilities": _tool_perplexity_capabilities,
        "perplexity_models": _tool_perplexity_models,
        "perplexity_model": _tool_perplexity_model,
        "perplexity_status": _tool_perplexity_status,
    })

    # The approval gate reads this. Asking/writing is OUTBOUND (the prompt leaves the machine
    # and the app spends the operator's quota), so those are not "safe"; the rest only read.
    if not hasattr(server, "_tool_safety"):
        server._tool_safety = {}
    server._tool_safety.update({
        "perplexity_ask": "moderate",
        "perplexity_write": "moderate",
        "perplexity_threads": "moderate",
        "perplexity_capabilities": "safe",
        "perplexity_model": "moderate",
        "perplexity_models": "safe",
        "perplexity_status": "safe",
    })
