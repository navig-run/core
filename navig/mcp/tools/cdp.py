"""
``cdp_*`` MCP tools — let the agent connect to and drive any Chromium-family
surface (Chrome/Edge/Brave + Electron apps like Discord & Notion) over CDP.

Thin wrappers over :mod:`navig.browser.cdp_actions` (the same implementation the
`navig cdp` CLI uses). Each handler is sync (the MCP dispatcher calls handlers
synchronously) and bridges to the async action via
:func:`navig.browser.cdp_runtime.run`, which owns the single loop that keeps CDP
sessions alive between tool calls.
"""

from __future__ import annotations

from typing import Any


def _run(coro):
    from navig.browser.cdp_runtime import run as _rt_run

    return _rt_run(coro)


# ────────────────────────── handlers ──────────────────────────


def _tool_cdp_targets(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return cdp_actions.targets(args.get("ports"))


def _tool_cdp_launch(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return cdp_actions.launch(
        args["app"],
        port=int(args.get("port", 9222)),
        force_restart=bool(args.get("force_restart", False)),
        user_data_dir=args.get("user_data_dir"),
        load_extension=args.get("load_extension"),
    )


def _tool_cdp_new(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return cdp_actions.new(
        app=args.get("app", "chrome"),
        port=int(args["port"]) if args.get("port") is not None else None,
        profile=args.get("profile"),
        load_extension=args.get("load_extension"),
        headless=bool(args.get("headless", False)),
        window_size=args.get("window_size"),
    )


def _tool_cdp_stop(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    all_ports = bool(args.get("all", False))
    return cdp_actions.stop(port=None if all_ports else int(args.get("port", 9222)),
                            all_ports=all_ports)


def _tool_cdp_detach(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    all_ports = bool(args.get("all", False))
    return cdp_actions.detach(port=None if all_ports else int(args.get("port", 9222)),
                              all_ports=all_ports)


def _tool_cdp_launched(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return cdp_actions.launched()


def _tool_cdp_snapshot(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return _run(cdp_actions.snapshot(int(args.get("port", 9222)),
                                     tab=args.get("tab"), url=args.get("url")))


def _tool_cdp_screenshot(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return _run(cdp_actions.screenshot(
        int(args.get("port", 9222)),
        out=args.get("out"),
        full_page=bool(args.get("full_page", False)),
        as_base64=bool(args.get("as_base64", False)),
        tab=args.get("tab"), url=args.get("url"),
    ))


def _tool_cdp_click(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    ref = args.get("ref")
    return _run(cdp_actions.click(
        int(args.get("port", 9222)),
        ref=int(ref) if ref is not None else None,
        x=args.get("x"),
        y=args.get("y"),
        button=args.get("button", "left"),
        tab=args.get("tab"), url=args.get("url"),
    ))


def _tool_cdp_type(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return _run(cdp_actions.type_text(int(args.get("port", 9222)), args.get("text", ""),
                                      tab=args.get("tab"), url=args.get("url")))


def _tool_cdp_key(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return _run(cdp_actions.key(int(args.get("port", 9222)), args.get("combo", "Enter"),
                                tab=args.get("tab"), url=args.get("url")))


def _tool_cdp_switch(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return _run(cdp_actions.switch(int(args.get("port", 9222)),
                                   tab=args.get("tab"), url=args.get("url")))


def _tool_cdp_scroll(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return _run(cdp_actions.scroll(
        int(args.get("port", 9222)),
        delta_y=float(args.get("delta_y", 300)),
        delta_x=float(args.get("delta_x", 0)),
    ))


def _tool_cdp_move(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return _run(cdp_actions.move(
        int(args.get("port", 9222)),
        x=float(args.get("x", 0)),
        y=float(args.get("y", 0)),
    ))


def _tool_cdp_eval(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return _run(cdp_actions.eval_js(int(args.get("port", 9222)), args.get("expression", "")))


def _tool_cdp_navigate(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return _run(cdp_actions.navigate(int(args.get("port", 9222)), args.get("url", "")))


def _tool_cdp_login(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return _run(cdp_actions.login(
        int(args.get("port", 9222)),
        domain=args.get("domain"),
        username=args.get("username"),
        open_url=args.get("open_url"),
        tab=args.get("tab"), url=args.get("url"),
        allow_insecure=bool(args.get("allow_insecure", False)),
        auto_submit=bool(args.get("auto_submit", True)),
    ))


def _tool_cdp_inject(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return _run(cdp_actions.inject_script(
        int(args.get("port", 9222)), args.get("script", ""),
        tab=args.get("tab"), url=args.get("url"),
    ))


def _tool_cdp_tabs(server: Any, args: dict[str, Any]) -> Any:  # noqa: ARG001
    from navig.browser import cdp_actions

    return _run(cdp_actions.tabs(int(args.get("port", 9222))))


# ────────────────────────── registration ──────────────────────────

_PORT_PROP = {"type": "integer", "description": "CDP debug port (default 9222).", "default": 9222}
_TAB_PROP = {"type": "integer", "description": "Tab index from cdp_tabs to act on."}
_URL_PROP = {"type": "string", "description": "Pick the tab whose URL contains this substring."}


def register(server: Any) -> None:
    """Register the cdp_* browser/Electron control tools."""
    server.tools.update({
        "cdp_targets": {
            "name": "cdp_targets",
            "description": "List live CDP endpoints on localhost (Chrome/Edge/Brave + Electron apps).",
            "inputSchema": {
                "type": "object",
                "properties": {"ports": {"type": "array", "items": {"type": "integer"},
                                          "description": "Ports to scan (default 9222-9229)."}},
                "required": [],
            },
        },
        "cdp_launch": {
            "name": "cdp_launch",
            "description": (
                "Launch an app with a CDP debug port so it can be driven. app = "
                "chrome|edge|brave|discord|notion|slack|vscode or an executable path. "
                "Electron apps (discord/notion) need force_restart if already running "
                "(this CLOSES the running window and reopens with the logged-in profile)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "app": {"type": "string", "description": "App id or executable path."},
                    "port": _PORT_PROP,
                    "force_restart": {"type": "boolean", "default": False,
                                       "description": "Quit a running instance first (destructive)."},
                    "user_data_dir": {"type": "string", "description": "Optional profile dir (browsers)."},
                    "load_extension": {"type": "string", "description": "Unpacked extension folder(s) to load in isolation (path, or comma-separated list)."},
                },
                "required": ["app"],
            },
        },
        "cdp_snapshot": {
            "name": "cdp_snapshot",
            "description": "Accessibility snapshot with numeric refs; feed a ref to cdp_click.",
            "inputSchema": {"type": "object",
                            "properties": {"port": _PORT_PROP, "tab": _TAB_PROP, "url": _URL_PROP},
                            "required": []},
        },
        "cdp_screenshot": {
            "name": "cdp_screenshot",
            "description": "Screenshot the attached page (to a file, or base64 with as_base64=true).",
            "inputSchema": {
                "type": "object",
                "properties": {"port": _PORT_PROP,
                                "out": {"type": "string", "description": "Output filename."},
                                "full_page": {"type": "boolean", "default": False},
                                "as_base64": {"type": "boolean", "default": False},
                                "tab": _TAB_PROP, "url": _URL_PROP},
                "required": [],
            },
        },
        "cdp_click": {
            "name": "cdp_click",
            "description": "Click by a11y ref (from cdp_snapshot) or at viewport coordinates (x, y).",
            "inputSchema": {
                "type": "object",
                "properties": {"port": _PORT_PROP,
                                "ref": {"type": "integer", "description": "a11y ref id."},
                                "x": {"type": "number"}, "y": {"type": "number"},
                                "button": {"type": "string", "enum": ["left", "right", "middle"],
                                            "default": "left"},
                                "tab": _TAB_PROP, "url": _URL_PROP},
                "required": [],
            },
        },
        "cdp_type": {
            "name": "cdp_type",
            "description": "Type text into the currently focused element.",
            "inputSchema": {
                "type": "object",
                "properties": {"port": _PORT_PROP, "text": {"type": "string"},
                                "tab": _TAB_PROP, "url": _URL_PROP},
                "required": ["text"],
            },
        },
        "cdp_key": {
            "name": "cdp_key",
            "description": "Press a key or combination (e.g. Enter, Control+A, Escape).",
            "inputSchema": {
                "type": "object",
                "properties": {"port": _PORT_PROP, "combo": {"type": "string", "default": "Enter"},
                                "tab": _TAB_PROP, "url": _URL_PROP},
                "required": ["combo"],
            },
        },
        "cdp_scroll": {
            "name": "cdp_scroll",
            "description": "Scroll the page by a wheel delta (positive delta_y = down).",
            "inputSchema": {
                "type": "object",
                "properties": {"port": _PORT_PROP,
                                "delta_y": {"type": "number", "default": 300},
                                "delta_x": {"type": "number", "default": 0}},
                "required": [],
            },
        },
        "cdp_move": {
            "name": "cdp_move",
            "description": "Move the mouse to viewport coordinates (x, y).",
            "inputSchema": {
                "type": "object",
                "properties": {"port": _PORT_PROP, "x": {"type": "number"}, "y": {"type": "number"}},
                "required": ["x", "y"],
            },
        },
        "cdp_eval": {
            "name": "cdp_eval",
            "description": "Evaluate a JavaScript expression in the page and return the result.",
            "inputSchema": {
                "type": "object",
                "properties": {"port": _PORT_PROP, "expression": {"type": "string"}},
                "required": ["expression"],
            },
        },
        "cdp_navigate": {
            "name": "cdp_navigate",
            "description": "Navigate the attached page to a URL.",
            "inputSchema": {
                "type": "object",
                "properties": {"port": _PORT_PROP, "url": {"type": "string"}},
                "required": ["url"],
            },
        },
        "cdp_login": {
            "name": "cdp_login",
            "description": (
                "Auto-login on the attached page using a VAULTED website credential. "
                "Session-first: restores a saved authenticated session if present, "
                "else fills the login form. Strictly origin-bound + https-only; the "
                "password is injected server-side and is never returned. Omit `domain` "
                "to use the current page's domain. Returns a status "
                "(session_restored|logged_in|filled|no_credential|wrong_origin|…)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "port": _PORT_PROP,
                    "domain": {"type": "string",
                               "description": "Site host, e.g. github.com (default: current page)."},
                    "username": {"type": "string",
                                 "description": "Account, when the site has several."},
                    "open_url": {"type": "string",
                                 "description": "Navigate here first, then log in."},
                    "auto_submit": {"type": "boolean", "default": True},
                    "allow_insecure": {"type": "boolean", "default": False,
                                        "description": "Allow http (localhost/dev only)."},
                    "tab": _TAB_PROP, "url": _URL_PROP,
                },
                "required": [],
            },
        },
        "cdp_inject": {
            "name": "cdp_inject",
            "description": (
                "Register a persistent userscript on the attached browser (CDP "
                "Page.addScriptToEvaluateOnNewDocument) — re-runs at document-start "
                "on every navigation, current and future pages. Unlike cdp_eval "
                "(one-shot)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"port": _PORT_PROP, "script": {"type": "string"},
                               "tab": _TAB_PROP, "url": _URL_PROP},
                "required": ["script"],
            },
        },
        "cdp_tabs": {
            "name": "cdp_tabs",
            "description": "List EVERY open page in the browser (full inventory from raw CDP).",
            "inputSchema": {"type": "object", "properties": {"port": _PORT_PROP}, "required": []},
        },
        "cdp_switch": {
            "name": "cdp_switch",
            "description": "Make a specific open tab the active target (by tab index or url substring). "
                           "Sticks on the session so later cdp_* calls act on it.",
            "inputSchema": {"type": "object",
                            "properties": {"port": _PORT_PROP, "tab": _TAB_PROP, "url": _URL_PROP},
                            "required": []},
        },
        "cdp_new": {
            "name": "cdp_new",
            "description": "Open a COMPLETELY FRESH, isolated browser (own profile + auto-allocated "
                           "debug port). Use `profile` for a reusable named profile, omit for throwaway.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "app": {"type": "string", "default": "chrome",
                            "description": "chrome|edge|brave or a path."},
                    "port": {"type": "integer", "description": "Debug port (auto if omitted)."},
                    "profile": {"type": "string", "description": "Named persistent profile."},
                    "load_extension": {"type": "string", "description": "Unpacked extension folder(s) to load in isolation (path, or comma-separated list)."},
                    "headless": {"type": "boolean", "default": False, "description": "Launch without a visible window (opt-in; unblocks display-less/CI runs)."},
                    "window_size": {"type": "string", "description": "Pin the window/viewport to WxH, e.g. 1440x900 (deterministic, portable shots)."},
                },
                "required": [],
            },
        },
        "cdp_stop": {
            "name": "cdp_stop",
            "description": "Close a NAVIG-launched debug browser (turns the debug port OFF). "
                           "Set all=true to close every launched debug browser.",
            "inputSchema": {
                "type": "object",
                "properties": {"port": _PORT_PROP, "all": {"type": "boolean", "default": False}},
                "required": [],
            },
        },
        "cdp_detach": {
            "name": "cdp_detach",
            "description": "Disconnect NAVIG's session but LEAVE the browser open (port stays on).",
            "inputSchema": {
                "type": "object",
                "properties": {"port": _PORT_PROP, "all": {"type": "boolean", "default": False}},
                "required": [],
            },
        },
        "cdp_launched": {
            "name": "cdp_launched",
            "description": "List debug browsers NAVIG launched and whether each port is still live.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
    })

    server._tool_handlers.update({
        "cdp_targets": _tool_cdp_targets,
        "cdp_launch": _tool_cdp_launch,
        "cdp_snapshot": _tool_cdp_snapshot,
        "cdp_screenshot": _tool_cdp_screenshot,
        "cdp_click": _tool_cdp_click,
        "cdp_type": _tool_cdp_type,
        "cdp_key": _tool_cdp_key,
        "cdp_scroll": _tool_cdp_scroll,
        "cdp_move": _tool_cdp_move,
        "cdp_eval": _tool_cdp_eval,
        "cdp_navigate": _tool_cdp_navigate,
        "cdp_login": _tool_cdp_login,
        "cdp_inject": _tool_cdp_inject,
        "cdp_tabs": _tool_cdp_tabs,
        "cdp_switch": _tool_cdp_switch,
        "cdp_new": _tool_cdp_new,
        "cdp_stop": _tool_cdp_stop,
        "cdp_detach": _tool_cdp_detach,
        "cdp_launched": _tool_cdp_launched,
    })

    # Safety classification consulted by the approval gate (see
    # navig.mcp_server._gate_tool). "safe" tools (read-only inventory/snapshot)
    # are never gated. "dangerous" tools gate under the default policy;
    # "moderate" tools gate only under CONFIRM_ALL / OWNER_ONLY.
    if not hasattr(server, "_tool_safety"):
        server._tool_safety = {}
    server._tool_safety.update({
        "cdp_eval": "dangerous",      # arbitrary JS — credential-exfiltration vector
        "cdp_launch": "dangerous",    # force_restart CLOSES a running window
        "cdp_stop": "dangerous",      # closes NAVIG-launched browsers
        "cdp_login": "dangerous",     # injects a real credential into a page
        "cdp_inject": "dangerous",    # persistent arbitrary JS on every navigation
        "cdp_navigate": "moderate",
        "cdp_new": "moderate",
        "cdp_click": "moderate",
        "cdp_type": "moderate",
        "cdp_key": "moderate",
        "cdp_scroll": "moderate",
        "cdp_move": "moderate",
        "cdp_detach": "moderate",
        # safe (unlisted → "safe"): cdp_targets, cdp_snapshot, cdp_screenshot,
        # cdp_tabs, cdp_switch, cdp_launched
    })
