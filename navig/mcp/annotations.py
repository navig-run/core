"""Titles, MCP safety annotations and schema strictness for every MCP tool.

A client cannot see into a tool's implementation, so it relies on three things the server
must tell it:

``title``
    a human label — otherwise the client renders ``navig_agent_goal_add`` at the user.

``annotations.readOnlyHint`` / ``destructiveHint`` / ``openWorldHint``
    what the call will do. Without them every client has to prompt for approval on
    ``navig_list_hosts`` (a pure read) and has no signal that ``cdp_eval`` can touch a
    logged-in page. This is the difference between an agent that is usable and one that
    asks four questions to answer one.

``inputSchema.additionalProperties: false``
    the schema's half of the argument validation in :mod:`navig.mcp.validation` — without
    it a mistyped argument is accepted and the caller is answered about something else.

The classification below is deliberately asymmetric: a tool is read-only only when it was
checked by hand against its handler, and **anything unclassified is destructive**. That is
the same rule the approval gate follows (no tool ships without a level), and for the same
reason — a wrong "read-only" is worse than a hundred conservative "ask first".
"""

from __future__ import annotations

from typing import Any

#: Tools that only read: no writes, no process control, no network mutation.
#: Curated by reading each handler; a name that merely *sounds* read-only is not enough
#: (``connector_gmail_search`` can send mail, so it is NOT here).
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        # inventory / wiki / docs
        "navig_list_hosts",
        "navig_list_apps",
        "navig_host_info",
        "navig_app_info",
        "navig_list_databases",
        "navig_list_wiki_pages",
        "navig_read_wiki_page",
        "navig_search_wiki",
        "navig_search_docs",
        "navig_get_context",
        # bay + blocks (browse/show only)
        "navig_bay_list",
        "navig_block_list",
        "navig_block_show",
        # agent + runtime (status only)
        "navig_agent_goal_list",
        "navig_agent_remediation_list",
        "navig_agent_service_status",
        "navig_agent_status_get",
        "navig_runtime_list_missions",
        "navig_runtime_list_nodes",
        "navig_runtime_list_receipts",
        "navig_runtime_trust_score",
        # memory (retrieval only)
        "memory_key_facts_retrieve",
        "memory_key_facts_stats",
        # recall (ingest writes, everything else reads)
        "recall_search",
        "recall_get",
        "recall_related",
        "recall_timeline",
        "recall_ask",
        "recall_decisions",
        "recall_forgotten_ideas",
        "recall_open_questions",
        "recall_source_excerpt",
        "recall_tables",
        # desktop / cdp (inspection only; screenshot writes a file, so it is excluded)
        "desktop_clipboard_get",
        "desktop_process_list",
        "desktop_registry_get",
        "desktop_registry_list",
        "desktop_find",
        "desktop_snapshot",
        "desktop_tree",
        # context-menu manager — discovery, planning and verification only
        "desktop_context_menu_audit",
        "desktop_context_menu_plan",
        "desktop_context_menu_status",
        "desktop_context_menu_verify",
        "cdp_launched",
        "cdp_snapshot",
        "cdp_tabs",
        "cdp_targets",
        # the Perplexity desktop app bridge — status/models only read the window;
        # perplexity_ask sends a question (outbound) and perplexity_model changes a setting
        "perplexity_status",
        "perplexity_models",
        # Spaces — metadata and one file, read through the linked-context guard. The gate
        # still classifies navig_space_read as `moderate` because file content enters the
        # conversation; privacy is a reason to ask, not a reason to call a read destructive.
        "navig_space_current",
        "navig_space_related",
        "navig_space_context",
        "navig_space_read",
        # the Dock (the operator's own logged-in browser) — reading it changes nothing.
        # dock_console/dock_network are EXCLUDED: their first call starts capturing and
        # `clear` empties the buffer, which is a side effect. dock_screenshot writes a file.
        "dock_status",
        "dock_tabs",
        "dock_text",
        "dock_snapshot",
        # connectors — fetch/list/health only; *_search and *_act can write
        "connector_list",
        "connector_health",
        # web reads
        "navig_web_fetch",
        "navig_web_search",
        "firecrawl_scrape",
        "firecrawl_search",
    }
)

#: Tools whose effect leaves the machine (network egress). A client may treat these as
#: "open world" and ask differently.
OPEN_WORLD_TOOLS: frozenset[str] = frozenset(
    {
        "navig_web_fetch",
        "navig_web_search",
        "firecrawl_scrape",
        "firecrawl_search",
        "firecrawl_crawl",
        # a question or a brief typed into the Perplexity app leaves the machine
        "perplexity_ask",
        "perplexity_write",
        # NOTE: connector_* tools are absent on purpose — they are generated per provider
        # and arrive pre-annotated by ``connector_annotations`` below. Listing them here
        # too would be two sources of truth for the same verdict.
    }
)

#: Connector tools are generated per provider × verb, so they cannot be hand-listed above.
#: Their verdict is derived from the verb instead. The direction of the default matters: a
#: read that is secretly a write is the worst lie this catalogue could tell (an agent would
#: mutate a mailbox believing it only searched), so an unrecognised verb stays destructive.
CONNECTOR_READ_OPERATIONS: frozenset[str] = frozenset({"search", "fetch"})
CONNECTOR_WRITE_OPERATIONS: frozenset[str] = frozenset({"act"})


def connector_annotations(operation: str) -> dict[str, bool]:
    """Annotations for one generated ``connector_<id>_<operation>`` tool.

    Two signals are deliberately kept apart. ``readOnlyHint``/``destructiveHint`` say what
    the call does to the remote system. NAVIG's approval gate (``_tool_safety`` in
    ``navig/mcp/tools/connectors.py``) separately classifies the same call as safe /
    moderate / dangerous — and that classification, not this annotation, decides whether
    it runs. Reading a private mailbox is therefore ``readOnlyHint: true`` and
    ``destructiveHint: false`` *and* ``moderate`` in the gate: confidential is not the same
    thing as destructive.

    Every connector verb calls a third-party API, so all three are ``openWorldHint``.
    """
    if operation in CONNECTOR_READ_OPERATIONS:
        return {
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": True,
            "idempotentHint": True,
        }
    # act — and any future/unrecognised verb, which must never inherit the read verdict.
    return {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}

#: Prefixes are common across ~134 tools; a title that repeats them is noise, so the
#: bundle name becomes a qualifier and ``navig_`` (the product itself) is dropped.
_PREFIX_QUALIFIERS: dict[str, str] = {
    "cdp": "CDP",
    "desktop": "Desktop",
    "connector": "Connector",
    "firecrawl": "Firecrawl",
    "perplexity": "Perplexity",
    "recall": "Recall",
    "memory": "Memory",
    "navig": "",
}

#: Hand-written titles where the derivation reads badly.
TITLE_OVERRIDES: dict[str, str] = {    "navig_run_command": "Run a NAVIG command",
    "navig_list_hosts": "List hosts",
    "navig_host_info": "Host details",
    "navig_list_apps": "List apps",
    "navig_app_info": "App details",
    "navig_get_context": "NAVIG context",
    "navig_web_fetch": "Fetch a URL",
    "navig_web_search": "Search the web",
    "cdp_eval": "CDP: Evaluate JavaScript",
    "perplexity_ask": "Perplexity: Delegate work",
    "perplexity_write": "Perplexity: Write an article or copy",
    "perplexity_threads": "Perplexity: Conversations",
    "perplexity_status": "Perplexity: App status",
    "desktop_powershell": "Desktop: Run PowerShell",
    "recall_ask": "Recall: Has the owner asked this before?",
    "memory_key_facts_remember": "Remember a key fact",
    "memory_key_facts_retrieve": "Search key facts",
    "memory_key_facts_stats": "Key fact stats",
    # Titles whose derivation reads badly or hides the verb. A client renders these at the
    # operator, so "Memory: Key facts forget" is the tool's fault, not the operator's.
    "memory_key_facts_forget": "Forget a key fact",
    "memory_key_facts_update": "Update a key fact",
    "navig_search_wiki": "Search the wiki",
    "navig_read_wiki_page": "Read a wiki page",
    "navig_list_wiki_pages": "List wiki pages",
    "cdp_key": "CDP: Press a key",
    "cdp_move": "CDP: Move the mouse",
    "cdp_type": "CDP: Type text",
    "desktop_registry_set": "Desktop: Set a registry value",
    "desktop_registry_delete": "Desktop: Delete a registry value",
}


#: Parameter descriptions for tools whose schema documented only some of them.
#: Kept here rather than edited into sixteen bundle files: it is catalogue metadata with
#: the same lifecycle as the titles above, and one table is reviewable where thirty
#: scattered edits are not. An existing description is never overwritten.
PARAM_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "navig_runtime_create_mission": {
        "title": "Mission title, shown in mission lists and execution receipts",
        "node_id": "Node that should execute the mission (default: the local node)",
        "capability": "Capability the mission needs from its node",
        "payload": "Mission input, passed through to the executing node",
        "priority": "Lower runs first; default 0",
    },
    "navig_runtime_mission_action": {
        "mission_id": "Mission to advance",
        "action": "Action to apply: start, succeed, fail:<message>, or cancel:<message>",
    },
    "navig_runtime_list_missions": {
        "node_id": "Only missions for this node",
        "status": "Only missions in this state (pending, running, succeeded, failed, cancelled)",
        "limit": "Maximum missions to return (default 50)",
    },
    "navig_runtime_list_receipts": {
        "node_id": "Only receipts produced by this node",
        "mission_id": "Only receipts for this mission",
        "limit": "Maximum receipts to return (default 50)",
    },
    "navig_runtime_trust_score": {
        "node_id": "Node to score from its receipt history",
    },
    "recall_source_excerpt": {
        "before": "Conversation turns to include before the cited message (default 3)",
        "after": "Conversation turns to include after the cited message (default 3)",
    },
    "cdp_screenshot": {
        "full_page": "Capture the whole scrollable page instead of just the viewport",
        "as_base64": "Return base64 image data instead of writing a file",
    },
    "cdp_click": {
        "x": "Viewport X coordinate; omit when clicking by accessibility ref",
        "y": "Viewport Y coordinate; omit when clicking by accessibility ref",
        "button": "Mouse button: left (default), right, or middle",
    },
    "cdp_type": {"text": "Text to type into the currently focused element"},
    "cdp_key": {"combo": "Key or chord to press, e.g. Enter or Control+A"},
    "cdp_scroll": {
        "delta_y": "Wheel delta: positive scrolls down",
        "delta_x": "Horizontal wheel delta: positive scrolls right",
    },
    "cdp_move": {"x": "Viewport X coordinate", "y": "Viewport Y coordinate"},
    "cdp_eval": {"expression": "JavaScript expression to evaluate in the page"},
    "cdp_navigate": {"url": "URL to load in the attached tab"},
    "cdp_login": {"auto_submit": "Submit the login form automatically after filling it"},
    "cdp_inject": {"script": "User script source to register on the attached browser"},
    "cdp_stop": {"all": "Stop every NAVIG-launched browser, not just the current one"},
    "cdp_detach": {"all": "Detach from every attached tab, not just the current one"},
    # Dock (browser-control bridge) — these tools landed with only some parameters
    # documented. Without these entries a model has to guess what `filter` or
    # `deadline_ms` accepts, which is exactly the failure this table exists to prevent.
    "dock_release": {"close": "Close the Dock session after releasing the tab (default: keep it open)"},
    "dock_snapshot": {"filter": "Only return elements whose text or role matches this substring"},
    "dock_text": {"max_chars": "Truncate the extracted text to this many characters"},
    "dock_act": {"deadline_ms": "Milliseconds to wait for the action to complete"},
    "dock_screenshot": {
        "ref": "Element ref to capture instead of the whole viewport",
        "max_width": "Downscale the image to at most this width in pixels",
        "quality": "Compression quality (1-100) for the returned image",
    },
    "dock_console": {
        "only_errors": "Only return error-level console entries",
        "pattern": "Only return entries whose text matches this substring",
        "clear": "Clear the buffered console entries after reading them",
        "limit": "Maximum entries to return",
    },
    "dock_network": {
        "failed_only": "Only return failed requests (status >= 400 or no response)",
        "url_pattern": "Only return requests whose URL matches this substring",
        "clear": "Clear the buffered requests after reading them",
        "limit": "Maximum requests to return",
    },
}

#: Descriptions that were too terse to tell an agent when to use the tool. Only applied
#: when the authored description is shorter than the replacement.
#:
#: Batch 1 (catalogue audit): every entry below was rewritten after reading the handler —
#: each one now says what comes back and which sibling continues the workflow, because
#: those two facts are what the evaluation's "missing selection guidance" and "missing
#: output expectations" findings are about. Do not add a sentence here that the handler
#: does not do: the inventory script records descriptions, and a lie in this table reaches
#: every client.
DESCRIPTION_OVERRIDES: dict[str, str] = {
    "navig_agent_goal_add": "Create an autonomous agent goal that can run unattended; start it with navig_agent_goal_start.",
    "navig_agent_goal_cancel": "Cancel an autonomous agent goal by ID; it stops running and its state becomes cancelled.",
    "navig_agent_remediation_retry": "Retry a persisted remediation action by ID, after fixing whatever made it fail.",
    "navig_agent_service_install": "Install NAVIG's agent as an OS service (systemd, launchd or a Windows service).",
    "navig_agent_service_uninstall": "Uninstall the NAVIG agent OS service so it no longer starts at boot.",
    "navig_runtime_list_nodes": "List registered NAVIG Nodes with their capabilities and last-seen state.",
    "memory_key_facts_remember": "Store a durable key fact in persistent memory, with its source and confidence.",
    "desktop_clipboard_set": "Write text to the Windows clipboard, replacing whatever it currently holds.",
    # ── wiki: search → read, and the fact that a missing wiki is reported, not empty ──
    "navig_search_wiki": (
        "Search NAVIG's wiki for pages about a topic. Read-only. Returns matching pages with "
        "their path and a short excerpt — pass a path to navig_read_wiki_page for the full text. "
        "Reports 'Wiki not initialized' when the space has no wiki."
    ),
    "navig_read_wiki_page": (
        "Read one wiki page by its path — e.g. 'knowledge/concepts/overview' — as returned by "
        "navig_search_wiki or navig_list_wiki_pages. Read-only. Returns {path, content} with the "
        "page's markdown; an unknown path returns an error rather than an empty page."
    ),
    "navig_list_wiki_pages": (
        "List the wiki's pages, optionally only one folder (knowledge, technical, hub, external). "
        "Read-only. Returns page entries with path and title; pass a path to "
        "navig_read_wiki_page to read one."
    ),
    # ── the generic executor: it is NOT "any navig verb", and saying so was a lie ────
    "navig_run_command": (
        "Run one READ-ONLY NAVIG command (host/app/db/wiki/tunnel/backup list|show|search, "
        "status, version, help) and return its captured output. Write `command` as 'host list' "
        "or as command='host' with args=['list'] — both issue the same command. Returns "
        "{stdout, stderr, returncode}; returncode is the CLI's own exit status (for `status`, "
        "non-zero can mean 'degraded', not that the call failed), and anything outside the "
        "read-only set is refused with the allowed list. A verb that changes state needs its "
        "dedicated tool (navig_bay_acquire, navig_runtime_create_mission, navig_block_apply) "
        "or the operator's CLI."
    ),
    # ── runtime: create → action, and what a receipt is ──────────────────────────────
    "navig_runtime_create_mission": (
        "Create a Mission (the unit of work that produces execution receipts) on a Node. Returns "
        "{mission, ok: true}; pass mission.id to navig_runtime_mission_action to start it. Call "
        "navig_runtime_list_nodes first when unsure which node should execute it."
    ),
    "navig_runtime_list_receipts": (
        "List ExecutionReceipts, optionally filtered by node or mission. Read-only. Returns "
        "{receipts, count}; each receipt carries its id, mission_id, node_id and outcome — the "
        "audit trail that navig_runtime_trust_score is computed from."
    ),
    # ── agent diagnostics: writes a report only when asked ──────────────────────────
    "navig_agent_learning_run": (
        "Analyze the last N days of agent debug/remediation logs and return recurring error "
        "patterns with recommendations — diagnostics only, nothing is restarted. With export=true "
        "it also writes ~/.navig/workspace/error-patterns.json, which is why this is not read-only. "
        "Follow up with navig_agent_remediation_retry for a specific failed action."
    ),
    # ── CDP: every verb needs the page it acts on, and the next verb to call ─────────
    "cdp_click": (
        "Click a button, link, checkbox or any element in the attached page — by the "
        "accessibility ref from cdp_snapshot, or at viewport coordinates (x, y). Returns the "
        "dispatch acknowledgement; use cdp_snapshot afterwards to see what changed and "
        "cdp_type to fill a field."
    ),
    "cdp_eval": (
        "Evaluate a JavaScript expression in the attached page and return its value. Requires a "
        "page attached through cdp_targets / cdp_tabs (or launched with cdp_launch). Gated: on a "
        "logged-in page the expression can read what the operator sees, so never move secrets "
        "through it — cdp_snapshot is the read-only way to inspect the page."
    ),
    "cdp_key": (
        "Press a key or chord (Enter, Control+A, Escape) in the attached page. Returns the "
        "dispatch acknowledgement. Focus the target first with cdp_click, send the chord, then "
        "cdp_snapshot to verify the result."
    ),
    "cdp_move": (
        "Move the mouse to viewport coordinates (x, y) in the attached page — this does not "
        "click. Returns an acknowledgement; use cdp_click to actually click, and cdp_snapshot "
        "for the element coordinates to aim at."
    ),
    "cdp_type": (
        "Type text into the element that currently has focus in the attached page. Returns an "
        "acknowledgement; click the field with cdp_click first, then cdp_type, then cdp_snapshot "
        "to confirm what landed."
    ),
    "cdp_navigate": (
        "Load a URL in the attached page and wait for the load event. Returns the navigation "
        "result; call cdp_snapshot afterwards to see the rendered page, or cdp_tabs to check "
        "where you ended up."
    ),
    "cdp_detach": (
        "Disconnect NAVIG's session but LEAVE the browser running (its debug port stays on). "
        "Returns {ok, detached} naming the port released, or 'all'. Use cdp_stop to actually "
        "close a NAVIG-launched browser, and cdp_targets to see what is still reachable afterwards."
    ),
    "cdp_stop": (
        "Close a NAVIG-launched debug browser and turn its debug port off. Set all=true to close "
        "every browser NAVIG launched; returns ok once the port is proven closed. Use cdp_detach "
        "instead when the browser should stay open — cdp_launched lists what NAVIG started."
    ),
    # ── web: the three search paths are easy to confuse ──────────────────────────────
    "firecrawl_search": (
        "Search the web through Firecrawl and return the result list (url, title, snippet); with "
        "scrapeInline=true each result also carries page markdown. Requires a Firecrawl key — "
        "navig_web_search works without one (it falls back), and firecrawl_scrape reads a URL you "
        "already have."
    ),
    # ── memory: soft delete and supersede are not what the names imply ──────────────
    # NOTE: recall_tables is NOT overridden here on purpose. Its authored description is
    # longer than any concise replacement (it carries the citation boilerplate every recall
    # tool shares), and this table only rewrites a description that is shorter — so an entry
    # would be dead config. Its benchmark miss (rank 2 behind recall_search) is recorded in
    # reports/quality/selection-benchmark.json instead of being papered over.
    "memory_key_facts_forget": (
        "Mark a stored key fact as deleted (soft delete): it stops appearing in "
        "memory_key_facts_retrieve but the row is kept, so memory_key_facts_stats still counts it "
        "as deleted. Returns {success, fact_id}."
    ),
    "memory_key_facts_update": (
        "Replace a fact's text, superseding the old wording instead of editing it in place. "
        "Returns {id (the new fact), old_id, status: 'updated'}; memory_key_facts_stats counts the "
        "old one as superseded."
    ),
    # ── inventory / desktop: state the contract, not just the verb ───────────────────
    "navig_list_databases": (
        "List the database connections configured in NAVIG. Read-only. Returns one entry per "
        "configured connection (name and settings) and does not connect to any of them."
    ),
    "desktop_registry_set": (
        "Create or update a Windows Registry value. Returns {success, path, name, type}, or an "
        "access-denied error when the key is protected. Read the current value with "
        "desktop_registry_get first; desktop_registry_delete removes one."
    ),
}


def title_for(name: str) -> str:
    """A human label for *name*, e.g. ``navig_list_hosts`` → ``List hosts``."""
    override = TITLE_OVERRIDES.get(name)
    if override:
        return override
    parts = name.split("_")
    qualifier = _PREFIX_QUALIFIERS.get(parts[0]) if parts else None
    if qualifier is not None:
        parts = parts[1:]
    words = [p for p in parts if p]
    if not words:
        return name
    text = " ".join(words)
    text = text[:1].upper() + text[1:]
    return f"{qualifier}: {text}" if qualifier else text


def annotations_for(name: str, level: str | None) -> dict[str, bool]:
    """MCP tool annotations for one tool.

    ``destructiveHint`` is true for everything that is not provably read-only — including
    tools the approval gate considers ``safe`` (writing a memory fact is safe and still
    not read-only).
    """
    readonly = name in READ_ONLY_TOOLS
    hints: dict[str, bool] = {
        "readOnlyHint": readonly,
        "destructiveHint": not readonly,
    }
    if name in OPEN_WORLD_TOOLS:
        hints["openWorldHint"] = True
    if readonly:
        # A read can be repeated freely; a write must not be described as idempotent.
        hints["idempotentHint"] = True
    return hints


#: Tools whose handler returns a shape we know, so `outputSchema` can be declared.
#: A tool NOT listed here returns something the server does not promise — declaring a
#: schema for it would make `structuredContent` wrong rather than merely absent.
OUTPUT_SCHEMA_KINDS: dict[str, str] = {
    "navig_list_hosts": "list",
    "navig_list_apps": "list",
    "navig_list_databases": "list",
    "navig_list_wiki_pages": "list",
    "navig_search_wiki": "list",
    "navig_search_docs": "list",
    "memory_key_facts_retrieve": "list",
    "navig_host_info": "object",
    "navig_app_info": "object",
    "navig_get_context": "object",
    "navig_agent_status_get": "object",
    "navig_agent_service_status": "object",
    "memory_key_facts_stats": "object",
}


def output_schema(kind: str) -> dict[str, Any]:
    """The `outputSchema` for a tool whose result shape is known.

    ``list``  → ``{"items": [...]}``; ``object`` → the dict as-is. Both allow extra
    properties on purpose: the schemas describe the envelope, not every field, and a
    client must not reject a result because a provider added one.
    """
    if kind == "list":
        return {
            "type": "object",
            "properties": {
                "items": {"type": "array", "description": "The rows this tool returned"},
            },
            "required": ["items"],
            "additionalProperties": True,
        }
    return {"type": "object", "additionalProperties": True}


def annotate_tools(tools: dict[str, dict[str, Any]], safety: dict[str, str] | None = None) -> None:
    """Add titles, annotations, schema strictness and output schemas to every tool.

    Called once, after every bundle has registered, so dynamically generated tools
    (``connector_*``) are covered by the same conservative default as static ones.

    Fills only what a bundle left blank: a generator that already set ``title`` or
    ``annotations`` owns that verdict. The connector bridge does, because it is the only
    code that knows each provider's capability matrix (see ``connector_annotations``).
    """
    safety = safety or {}
    for name, tool in tools.items():
        if not isinstance(tool, dict):  # a test double; leave it alone
            continue
        tool.setdefault("title", title_for(name))
        tool.setdefault("annotations", annotations_for(name, safety.get(name)))

        better = DESCRIPTION_OVERRIDES.get(name)
        if better and len(tool.get("description") or "") < len(better):
            tool["description"] = better

        # Structured output: a client that understands `outputSchema` reads
        # `structuredContent` instead of re-parsing the text block. Declared only for
        # tools whose handler returns a known shape — declaring it for anything else
        # would make `structuredContent` a lie.
        kind = OUTPUT_SCHEMA_KINDS.get(name)
        if kind:
            tool.setdefault("outputSchema", output_schema(kind))

        schema = tool.get("inputSchema")
        if isinstance(schema, dict) and schema.get("type") == "object":
            # Respect a tool that deliberately allows extras; add strictness only where
            # nothing was declared, so the schema and the validator agree.
            schema.setdefault("additionalProperties", False)

            props = schema.get("properties")
            if isinstance(props, dict):
                for prop, text in PARAM_DESCRIPTIONS.get(name, {}).items():
                    spec = props.get(prop)
                    if isinstance(spec, dict) and not spec.get("description"):
                        spec["description"] = text
