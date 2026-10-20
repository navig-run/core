"""The Dock bridge protocol — one browser-control vocabulary for every caller.

Wire frames (JSON text over the Dock's WebSocket, ``PROTOCOL_VERSION``):

* Dock → daemon ``{"t":"hello","proto":1,"token":…,"version":…,"caps":[…]}``
* daemon → Dock ``{"t":"welcome","proto":1,"session":…}`` (or a close code, below)
* daemon → Dock ``{"t":"call","id":…,"tool":…,"args":{…},"deadline_ms":…}``
* Dock → daemon ``{"t":"result","id":…,"ok":bool,"data"|"error":{category,message},"timing":{…}}``
* daemon → Dock ``{"t":"cancel","id":…}`` · either way ``{"t":"ping"}`` / ``{"t":"pong"}``
* Dock → daemon ``{"t":"event","kind":"stopped"|"paused"|"resumed"|…}``

Call ids are never re-sent: a call in flight when the socket drops fails with
``effects`` = ``"uncertain"`` (mutations) or ``"none"`` (reads), so a reconnect can
never run an action twice.

Page actions reuse :func:`navig.agent.browser_turn.validate_actions`, so an outside agent
and the side panel's own model drive pages with exactly the same verbs and rules.
"""

from __future__ import annotations

import os
from typing import Any

PROTOCOL_VERSION = 1

# Close codes (4000–4999 are application codes).
CLOSE_BAD_HELLO = 4001
CLOSE_SUPERSEDED = 4002
CLOSE_UNAUTHORIZED = 4003

MAX_IN_FLIGHT = 32                 # backpressure: more concurrent calls → "busy"
DEFAULT_DEADLINE_MS = 30_000
MAX_DEADLINE_MS = 120_000
HELLO_TIMEOUT_S = 5.0

# Page verbs an outside agent may batch (the side panel's done/ask_user/read_page are
# conversation verbs, meaningless here: the caller reads the page with `snapshot`).
ACT_VERBS = frozenset({
    "click", "type", "key", "scroll", "hover", "select", "navigate", "wait", "get_text",
    "fill", "wait_for", "extract", "upload",
})

# tool → is it a mutation? (mutations: per-tab serialised, never retried, "uncertain" on drop)
TOOLS: dict[str, bool] = {
    "status": False,
    "tabs": False,
    "snapshot": False,
    "text": False,
    "screenshot": False,
    "console": False,
    "network": False,
    "open": True,
    "release": True,
    "act": True,
}

ERROR_CATEGORIES = (
    "unavailable",     # no Dock connected / paused by the user
    "invalid",         # bad arguments (nothing ran)
    "out_of_scope",    # tab is not in the agents' group
    "busy",            # too many calls, or a side-panel run owns the tab
    "stale",           # the page changed under the caller (navigation / gone ref)
    "stopped",         # the user pressed Stop
    "timeout",         # deadline passed
    "page",            # the page/browser refused (blocked URL, debugger in use…)
    "disconnected",    # the socket dropped mid-call
    "internal",
)


class DockCallError(Exception):
    """A tool call that did not complete. ``effects`` says whether anything may have run."""

    def __init__(self, category: str, message: str, *, effects: str = "none", data: Any = None):
        super().__init__(message)
        self.category = category if category in ERROR_CATEGORIES else "internal"
        self.message = message
        self.effects = effects
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"category": self.category, "message": self.message, "effects": self.effects}
        if self.data is not None:
            out["data"] = self.data
        return out


def _tab_id(args: dict[str, Any]) -> int:
    raw = args.get("tab_id")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise DockCallError("invalid", "tab_id must be a tab id from dock_tabs or dock_open")
    return raw


def validate_call(tool: str, args: Any) -> dict[str, Any]:
    """Normalised args for *tool*, or :class:`DockCallError` ``invalid`` (nothing runs)."""
    from navig.agent.browser_turn import validate_actions  # noqa: PLC0415

    if tool not in TOOLS:
        raise DockCallError("invalid", f"unknown tool {tool!r}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise DockCallError("invalid", "args must be an object")
    if tool in ("status", "tabs"):
        return {}
    if tool == "open":
        url = args.get("url", "")
        if url and (not isinstance(url, str) or not url.startswith(("http://", "https://"))):
            raise DockCallError("invalid", "url must be an http(s) address")
        return {"url": url or "", "active": bool(args.get("active", True))}
    if tool == "release":
        return {"tab_id": _tab_id(args), "close": bool(args.get("close", True))}
    if tool == "snapshot":
        flt = args.get("filter", "interactive")
        if flt not in ("interactive", "all"):
            raise DockCallError("invalid", "filter must be interactive or all")
        out = {"tab_id": _tab_id(args), "filter": flt}
        since = args.get("since")
        if since is not None:
            if isinstance(since, bool) or not isinstance(since, int):
                raise DockCallError("invalid", "since must be the rev from an earlier dock_snapshot")
            out["since"] = since
        return out
    if tool == "screenshot":
        out = {"tab_id": _tab_id(args), "max_width": _int(args, "max_width", 1280, 200, 1920),
               "quality": _int(args, "quality", 70, 20, 95)}
        if args.get("ref") is not None:
            if not isinstance(args["ref"], str) or not args["ref"].startswith("ref_"):
                raise DockCallError("invalid", "ref must be a [ref_N] from dock_snapshot")
            out["ref"] = args["ref"]
        return out
    if tool in ("console", "network"):
        out = {"tab_id": _tab_id(args), "clear": bool(args.get("clear", False)),
               "limit": _int(args, "limit", 100, 1, 500)}
        if tool == "console":
            out["only_errors"] = bool(args.get("only_errors", False))
            out["pattern"] = _str(args, "pattern", 200)
        else:
            out["failed_only"] = bool(args.get("failed_only", False))
            out["url_pattern"] = _str(args, "url_pattern", 500)
        return out
    if tool == "text":
        n = args.get("max_chars", 12_000)
        if isinstance(n, bool) or not isinstance(n, int):
            raise DockCallError("invalid", "max_chars must be an integer")
        return {"tab_id": _tab_id(args), "max_chars": max(200, min(n, 50_000))}
    # act
    steps = args.get("actions")
    if not isinstance(steps, list) or not steps:
        raise DockCallError("invalid", "actions must be a non-empty list")
    checked = validate_actions(steps, allowed=ACT_VERBS)
    if checked.errors:
        raise DockCallError("invalid", "; ".join(checked.errors))
    for i, step in enumerate(checked.actions):
        if step["action"] == "upload":
            # The daemon and the browser share this computer: a path that is not a file
            # here would reach the site as an empty upload. Refuse it before anything runs.
            missing = [f for f in step["files"] if not (os.path.isabs(f) and os.path.isfile(f))]
            if missing:
                raise DockCallError("invalid", f"step {i + 1} (upload): not a file on this computer: {missing[0]}")
    observe = args.get("observe", True)
    if observe != "diff":
        observe = bool(observe)
    out: dict[str, Any] = {"tab_id": _tab_id(args), "actions": checked.actions, "observe": observe}
    doc = args.get("doc")
    if doc is not None:
        if not isinstance(doc, str) or len(doc) > 64:
            raise DockCallError("invalid", "doc must be the doc id from dock_snapshot")
        out["doc"] = doc
    return out


def _int(args: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    raw = args.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise DockCallError("invalid", f"{key} must be a number")
    return max(lo, min(int(raw), hi))


def _str(args: dict[str, Any], key: str, limit: int) -> str:
    raw = args.get(key, "")
    if not isinstance(raw, str) or len(raw) > limit:
        raise DockCallError("invalid", f"{key} must be text up to {limit} characters")
    return raw


def call_id(raw: Any) -> str | None:
    """A caller-chosen call id (so the caller can cancel it), or None to mint one."""
    if isinstance(raw, str) and len(raw) == 16 and all(c in "0123456789abcdef" for c in raw):
        return raw
    return None


def deadline_ms(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULT_DEADLINE_MS
    return max(1_000, min(int(raw), MAX_DEADLINE_MS))
