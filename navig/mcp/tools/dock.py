"""``dock_*`` MCP tools — drive the operator's OWN Chrome (logged-in tabs) through NAVIG Dock.

A different backend from ``cdp_*`` on purpose: ``cdp_*`` drives a separate debug-port
browser NAVIG launched; ``dock_*`` drives the user's everyday browser through the paired
Dock extension. Neither silently falls back to the other — a caller that asked for the
user's browser must never end up acting in a different profile.

Each call is one keep-alive HTTP request to the local gateway (``POST /dock/call``), which
relays it over the Dock's socket. Tool names and argument rules live in
``navig.dock.protocol``; this module only describes them to MCP clients.
"""

from __future__ import annotations

import json
import secrets
import threading
from typing import Any

# One agent session per MCP server process: the Dock leases the tabs a session acts in, so
# two agents (two Claude Code windows, Claude Code + Codex) never act in the same tab at once.
_AGENT_SESSION = secrets.token_hex(6)

_session_lock = threading.Lock()
_session: Any = None
_base: str | None = None
_headers: dict[str, str] | None = None


def _http() -> tuple[Any, str, dict[str, str]]:
    """A pooled session + cached gateway address/headers (resolving them costs a probe)."""
    global _session, _base, _headers
    with _session_lock:
        if _session is None:
            import requests  # noqa: PLC0415

            _session = requests.Session()
        if _base is None or _headers is None:
            from navig.gateway_client import (  # noqa: PLC0415
                gateway_base_url,
                gateway_request_headers,
            )

            _base, _headers = gateway_base_url(), gateway_request_headers()
        return _session, _base, _headers


def _forget_address() -> None:
    global _base, _headers
    with _session_lock:
        _base = _headers = None


def _request(method: str, path: str, payload: dict[str, Any] | None, timeout_s: float) -> dict[str, Any]:
    import requests  # noqa: PLC0415

    from navig.gateway_client import unwrap_envelope  # noqa: PLC0415

    for attempt in (1, 2):
        session, base, headers = _http()
        try:
            resp = session.request(method, f"{base}{path}", json=payload, headers=headers, timeout=timeout_s)
        except requests.ConnectionError:
            _forget_address()  # the gateway may have moved ports or restarted — resolve again once
            if attempt == 2:
                return {"ok": False, "error": {"category": "unavailable", "effects": "none",
                                               "message": "The NAVIG daemon is not running (navig service start)."}}
            continue
        if resp.status_code == 401 and attempt == 1:
            _forget_address()  # token re-minted at gateway boot — re-read it once
            continue
        try:
            body = unwrap_envelope(resp.json())
        except ValueError:
            body = None
        if resp.status_code != 200 or not isinstance(body, dict):
            return {"ok": False, "error": {"category": "internal", "effects": "none",
                                           "message": f"gateway answered HTTP {resp.status_code}"}}
        return body
    return {"ok": False, "error": {"category": "internal", "effects": "none", "message": "unreachable"}}


def _cancel(call_id: str) -> None:
    """Tell the daemon to stop call *call_id* (MCP notifications/cancelled)."""
    import requests  # noqa: PLC0415

    _, base, headers = _http()
    requests.post(f"{base}/dock/cancel", json={"id": call_id}, headers=headers, timeout=3)


def _call(server: Any, tool: str, args: dict[str, Any], deadline_ms: int = 30_000) -> dict[str, Any]:
    actor = str(getattr(server, "client_name", "") or "")
    call_id = secrets.token_hex(8)
    on_cancel = getattr(server, "on_cancel", None)
    if callable(on_cancel):
        on_cancel(lambda: _cancel(call_id))
    payload = {"tool": tool, "args": args, "deadline_ms": deadline_ms, "actor": actor, "id": call_id,
               "session": _AGENT_SESSION}
    return _compact(_request("POST", "/dock/call", payload, timeout_s=deadline_ms / 1000 + 5))


def _compact(body: dict[str, Any]) -> dict[str, Any]:
    """Compact JSON for the agent (outlines are long; indentation is pure token cost)."""
    text = json.dumps(body, separators=(",", ":"), ensure_ascii=False, default=str)
    return {"ok": body.get("ok"), "error": body.get("error"),
            "_mcp_content": [{"type": "text", "text": text}]}


def _args(args: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: args[k] for k in keys if k in args}


def _tool_dock_status(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    return _request("GET", "/dock/status", None, timeout_s=5)


def _tool_dock_tabs(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    return _call(server, "tabs", {}, 10_000)


def _tool_dock_open(server: Any, args: dict[str, Any]) -> Any:
    return _call(server, "open", _args(args, "url", "active"), 30_000)


def _tool_dock_release(server: Any, args: dict[str, Any]) -> Any:
    return _call(server, "release", _args(args, "tab_id", "close"), 10_000)


def _tool_dock_snapshot(server: Any, args: dict[str, Any]) -> Any:
    return _call(server, "snapshot", _args(args, "tab_id", "filter", "since"), 20_000)


def _tool_dock_text(server: Any, args: dict[str, Any]) -> Any:
    return _call(server, "text", _args(args, "tab_id", "max_chars"), 20_000)


def _tool_dock_screenshot(server: Any, args: dict[str, Any]) -> Any:
    actor = str(getattr(server, "client_name", "") or "")
    payload = {"tool": "screenshot", "args": _args(args, "tab_id", "ref", "max_width", "quality"),
               "deadline_ms": 20_000, "actor": actor, "id": secrets.token_hex(8), "session": _AGENT_SESSION}
    body = _request("POST", "/dock/call", payload, timeout_s=25)
    shot = (body.get("data") or {}) if body.get("ok") else {}
    if not shot.get("data"):
        return _compact(body)
    meta = {"ok": True, "width": shot.get("width"), "height": shot.get("height"), "timing": body.get("timing")}
    return {"ok": True, "_mcp_content": [
        {"type": "text", "text": json.dumps(meta, separators=(",", ":"))},
        {"type": "image", "data": shot["data"], "mimeType": shot.get("mime", "image/jpeg")},
    ]}


def _tool_dock_console(server: Any, args: dict[str, Any]) -> Any:
    return _call(server, "console", _args(args, "tab_id", "only_errors", "pattern", "clear", "limit"), 10_000)


def _tool_dock_network(server: Any, args: dict[str, Any]) -> Any:
    return _call(server, "network", _args(args, "tab_id", "failed_only", "url_pattern", "clear", "limit"), 10_000)


def _tool_dock_act(server: Any, args: dict[str, Any]) -> Any:
    deadline = args.get("deadline_ms") if isinstance(args.get("deadline_ms"), int) else 60_000
    return _call(server, "act", _args(args, "tab_id", "actions", "observe", "doc"), deadline)


_TAB = {"type": "integer", "description": "Tab id from dock_tabs or dock_open."}
_ACTION_HELP = (
    "Ordered steps, run one after another; the first failure stops the batch and later steps "
    "are skipped (results say which ran). Each step is {\"action\": verb, …}. TARGET = ref "
    "(a [ref_N] from dock_snapshot) OR a locator: role (+ name), testid, css, has_text or xpath "
    "(+ nth, 1-based) — a locator matching several elements fails as ambiguous, never guesses. "
    "Verbs: click {TARGET | x,y, button?, clicks?} · type {text, TARGET?, clear?, submit?} · "
    "fill {TARGET, value} (sets a field / checkbox / dropdown directly — fastest) · key {keys e.g. "
    "\"Control+a\"} · scroll {direction up|down, amount? | TARGET} · hover {TARGET | x,y} · "
    "select {TARGET, value} · navigate {url | back | forward | reload} · wait {ms ≤ 5000} · "
    "wait_for {locator | url_contains, gone?, timeout_ms ≤ 15000} (event driven) · "
    "extract {TARGET, attr?, max?} (text / links of matching elements) · get_text {} · "
    "upload {TARGET (a file input), files: [absolute paths on this computer]} (the user confirms on the page). "
    "click / type / key / navigate take dialog: accept|dismiss for an alert/confirm/prompt the step opens "
    "(default dismiss; prompt_text answers a prompt). Typing into a password or card field asks the user first."
)


def register(server: Any) -> None:
    server.tools.update({
        "dock_status": {
            "name": "dock_status",
            "description": "Is the user's own Chrome (NAVIG Dock) connected for agent control? Shows paused/connected "
                           "state and latency. Use the dock_* tools to act in the user's real, logged-in browser; "
                           "use cdp_* only for a separate debug browser.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        "dock_tabs": {
            "name": "dock_tabs",
            "description": "List the tabs agents may use: the 'NAVIG agents' tab group in the user's browser "
                           "(tabs you opened plus any the user dragged into the group). held_by says which agent "
                           "is working in a tab ('you', another agent, or null = free); acting in a tab another "
                           "agent holds fails as busy until it releases it or 10 idle minutes pass.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        "dock_open": {
            "name": "dock_open",
            "description": "Open a new tab in the user's browser inside the 'NAVIG agents' group and return its tab_id.",
            "inputSchema": {"type": "object", "properties": {
                "url": {"type": "string", "description": "http(s) address; empty opens a blank tab."},
                "active": {"type": "boolean", "default": True, "description": "Bring the tab to the front."},
            }, "required": []},
        },
        "dock_release": {
            "name": "dock_release",
            "description": "Hand a tab back when you are done. Tabs you opened are closed (close=false keeps them for "
                           "the user); tabs the user shared are only removed from the agents group, never closed.",
            "inputSchema": {"type": "object", "properties": {
                "tab_id": _TAB, "close": {"type": "boolean", "default": True},
            }, "required": ["tab_id"]},
        },
        "dock_snapshot": {
            "name": "dock_snapshot",
            "description": "Read a tab as a compact accessibility outline: one line per element with [ref_N] handles "
                           "for dock_act. Passwords and card numbers are redacted. Returns url, title, doc, rev and "
                           "tree — or, with since=<rev you hold>, only a diff (added / changed / removed lines).",
            "inputSchema": {"type": "object", "properties": {
                "tab_id": _TAB,
                "filter": {"type": "string", "enum": ["interactive", "all"], "default": "interactive"},
                "since": {"type": "integer", "description": "rev of your last snapshot of this page → diff only."},
            }, "required": ["tab_id"]},
        },
        "dock_text": {
            "name": "dock_text",
            "description": "The readable text of a tab's main content (article/main or body).",
            "inputSchema": {"type": "object", "properties": {
                "tab_id": _TAB, "max_chars": {"type": "integer", "default": 12000},
            }, "required": ["tab_id"]},
        },
        "dock_act": {
            "name": "dock_act",
            "description": "Act in a tab with trusted input — up to 12 steps in ONE call. Returns per-step results "
                           "and (observe=true, the default) the page outline afterwards, so you rarely need a "
                           "separate dock_snapshot. Pass doc from the snapshot you read so a page that changed "
                           "under you is refused instead of clicked blindly.",
            "inputSchema": {"type": "object", "properties": {
                "tab_id": _TAB,
                "actions": {"type": "array", "items": {"type": "object"}, "description": _ACTION_HELP},
                "observe": {"description": "true: the page outline after the steps (default); \"diff\": only "
                                           "what changed; false: nothing.", "default": True},
                "doc": {"type": "string", "description": "The doc id from the snapshot the refs came from."},
                "deadline_ms": {"type": "integer", "default": 60000},
            }, "required": ["tab_id", "actions"]},
        },
    })
    server.tools.update({
        "dock_screenshot": {
            "name": "dock_screenshot",
            "description": "A JPEG of the tab's visible area (or one element: ref), at most max_width px wide. Works "
                           "in a background tab. Prefer dock_snapshot for acting — use this when layout or images "
                           "matter.",
            "inputSchema": {"type": "object", "properties": {
                "tab_id": _TAB, "ref": {"type": "string"},
                "max_width": {"type": "integer", "default": 1280}, "quality": {"type": "integer", "default": 70},
            }, "required": ["tab_id"]},
        },
        "dock_console": {
            "name": "dock_console",
            "description": "Console messages and uncaught errors of a tab. The first call starts capturing (reload the "
                           "page with dock_act navigate reload to see load-time errors).",
            "inputSchema": {"type": "object", "properties": {
                "tab_id": _TAB, "only_errors": {"type": "boolean"}, "pattern": {"type": "string"},
                "clear": {"type": "boolean"}, "limit": {"type": "integer", "default": 100},
            }, "required": ["tab_id"]},
        },
        "dock_network": {
            "name": "dock_network",
            "description": "Requests a tab made (url with secrets redacted, method, type, status, failures). The first "
                           "call starts capturing.",
            "inputSchema": {"type": "object", "properties": {
                "tab_id": _TAB, "failed_only": {"type": "boolean"}, "url_pattern": {"type": "string"},
                "clear": {"type": "boolean"}, "limit": {"type": "integer", "default": 100},
            }, "required": ["tab_id"]},
        },
    })
    server._tool_handlers.update({
        "dock_screenshot": _tool_dock_screenshot,
        "dock_console": _tool_dock_console,
        "dock_network": _tool_dock_network,
        "dock_status": _tool_dock_status,
        "dock_tabs": _tool_dock_tabs,
        "dock_open": _tool_dock_open,
        "dock_release": _tool_dock_release,
        "dock_snapshot": _tool_dock_snapshot,
        "dock_text": _tool_dock_text,
        "dock_act": _tool_dock_act,
    })
    # A browser call can take seconds (page loads, waits): run them off the stdio reader
    # so the client's next request — or its cancel — is never stuck behind one.
    if hasattr(server, "_concurrent_tools"):
        server._concurrent_tools.update(n for n in server.tools if n.startswith("dock_"))
    if not hasattr(server, "_tool_safety"):
        server._tool_safety = {}
    server._tool_safety.update({
        # Same classes as the matching cdp_* tools: acting is "moderate" (audited, and
        # gated under CONFIRM_ALL); the Dock adds its own visible overlay + Stop.
        "dock_act": "moderate",
        "dock_open": "moderate",
        "dock_release": "moderate",
        "dock_status": "safe",
        "dock_tabs": "safe",
        "dock_screenshot": "safe",
        "dock_console": "safe",
        "dock_network": "safe",
        "dock_snapshot": "safe",
        "dock_text": "safe",
    })
