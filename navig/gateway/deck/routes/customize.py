"""Customizations — one surface for what the brain actually runs.

Backs the navig OS "Customizations" app: agents, skills, instructions, hooks, MCP
servers and tools, read from the SAME sources the runtime reads, never a parallel copy.
Every section is a ``_load_*`` function; ``/customize/overview`` counts by calling those
same functions, so a sidebar badge can never disagree with the list it opens.

Honesty rules this module keeps (each one a defect found in the routes it replaces):

* a state we could not determine is ``None``/``"unknown"``, never a confident default;
* a section whose loader failed returns its ``error`` instead of an empty list;
* no control is offered for a setting nothing reads (the admin roster's
  ``agents.enabled`` toggles had no runtime reader, so none are offered here).

Routes (all under the deck auth middleware; mutations validate and 400 on bad input):

  GET   /api/deck/customize/overview
  GET   /api/deck/customize/agents
  GET   /api/deck/customize/instructions
  GET   /api/deck/customize/instructions/{name}
  POST  /api/deck/customize/instructions/{name}     {content, expected_mtime_ns?}
  GET   /api/deck/customize/hooks
  GET   /api/deck/customize/mcp
  PATCH /api/deck/customize/mcp/{name}              {enabled}
  POST  /api/deck/customize/mcp/{name}/connect
  POST  /api/deck/customize/mcp/{name}/disconnect
  GET   /api/deck/customize/tools
  PATCH /api/deck/customize/tools/{name}            {blocked}

Skills keep their existing routes (``/api/deck/skills``); only their count is here.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _ok(data: Any, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 400) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


async def _json_object(request: "web.Request") -> dict | None:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — any unparseable body is a 400
        return None
    return body if isinstance(body, dict) else None


def _section(fn) -> dict[str, Any]:
    """Run one loader; a failure becomes ``{"error": …}`` instead of an empty section."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — reported, never swallowed into "0 items"
        logger.warning("customize: %s failed: %s", getattr(fn, "__name__", fn), exc)
        return {"items": [], "error": str(exc)}


# ── Agents ─────────────────────────────────────────────────────────────────────


def _load_agents() -> dict[str, Any]:
    """The agents that shape behaviour: the active formation's, plus plugin subagents.

    Formation agents run (default agent, council). Plugin ``agents/*.md`` are listed for
    visibility and are labelled as such — NAVIG does not dispatch to them yet.
    """
    from navig.gateway.deck.routes.formations import _active_formation, _formation_payload
    from navig.plugins.cc_agents import discover_plugin_agents

    formation = _active_formation()
    payload = _formation_payload(formation) if formation else None
    items: list[dict[str, Any]] = []
    for agent in (payload or {}).get("agents", []):
        items.append({
            **agent,
            "source": "formation",
            "is_default": bool(payload and agent.get("id") == payload.get("default_agent")),
        })
    for agent in discover_plugin_agents():
        items.append({
            "id": agent["key"],
            "name": agent["label"],
            "role": agent.get("subtitle", ""),
            "model": agent.get("model", ""),
            "source": "plugin",
            "loaded": True,
            "is_default": False,
        })
    return {
        "items": items,
        "formation": (
            {k: payload[k] for k in ("id", "name", "description", "default_agent")}
            if payload else None
        ),
        "error": None,
    }


# ── Instructions (the workspace files the agent loads into its prompt) ─────────

#: name -> when the runtime loads it. Mirrors `_build_agent_context` in gateway/server.py
#: plus IDENTITY.md (resolved by the soul chain). A hard allowlist: these names are the
#: only files this route will ever read or write, which is the path-traversal guard.
INSTRUCTION_FILES: dict[str, str] = {
    "SOUL.md": "always (unless a persona or space SOUL overrides it)",
    "IDENTITY.md": "always (identity chain)",
    "AGENTS.md": "always",
    "USER.md": "always",
    "TOOLS.md": "always",
    "GUARDRAILS.md": "always (adds rules; never removes the built-in floor)",
    "MEMORY.md": "chat turns",
    "HEARTBEAT.md": "heartbeat runs",
}

_MAX_INSTRUCTION_BYTES = 256 * 1024


def _instruction_dirs() -> list[Path]:
    """Read order the runtime uses: the user workspace first, then the config one."""
    from navig.platform.paths import workspace_dir
    from navig.workspace_ownership import user_workspace_dir

    out: list[Path] = []
    for d in (user_workspace_dir(), workspace_dir()):
        if d not in out:
            out.append(d)
    return out


def _resolve_instruction(name: str) -> Path:
    """The file the runtime would read (first existing), else where a save will create it."""
    dirs = _instruction_dirs()
    for d in dirs:
        if (d / name).is_file():
            return d / name
    return dirs[0] / name


def _load_instructions() -> dict[str, Any]:
    items = []
    for name, loaded in INSTRUCTION_FILES.items():
        path = _resolve_instruction(name)
        exists = path.is_file()
        stat = path.stat() if exists else None
        items.append({
            "name": name,
            "path": str(path),
            "exists": exists,
            "size": stat.st_size if stat else 0,
            "mtime_ns": stat.st_mtime_ns if stat else None,
            "loaded": loaded,
        })
    soul_source = None
    try:
        from navig.personas.soul_loader import resolve_soul

        soul_source = resolve_soul().source
    except Exception as exc:  # noqa: BLE001 — informational only
        logger.debug("customize: soul source unavailable: %s", exc)
    return {"items": items, "soul_source": soul_source, "error": None}


# ── Hooks ──────────────────────────────────────────────────────────────────────


def _load_hooks() -> dict[str, Any]:
    from navig.hooks.registry import HookRegistry

    registry = HookRegistry()
    registry.load()
    items = [
        {
            "event": d.event.value,
            "command": d.command,
            "matcher": d.tool_filter,
            "timeout_seconds": d.timeout_seconds,
            "description": d.description,
            "source": d.source,
        }
        for d in registry.all_definitions()
    ]
    return {
        "items": items,
        "enabled": registry.enabled,
        "allow_network": registry.allow_network,
        "config_paths": [
            {"path": str(p), "exists": p.is_file()} for p in registry.config_paths()
        ],
        "error": None,
    }


# ── MCP servers ────────────────────────────────────────────────────────────────


def _agent_tool_counts() -> dict[str, int]:
    """``server -> tools the AGENT can call`` (registry toolset ``mcp:<server>``)."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    counts: dict[str, int] = {}
    for entry in _AGENT_REGISTRY.entries():
        if entry.toolset.startswith("mcp:"):
            server = entry.toolset[4:]
            counts[server] = counts.get(server, 0) + 1
    return counts


def _load_mcp(live_clients: dict | None) -> dict[str, Any]:
    """Every MCP server navig knows of, from all four places one can be declared.

    ``source`` says which, because they behave differently and only one is editable:

    * ``servers`` — ``mcp.servers``: connected by the gateway at boot; the deck writes it.
    * ``agent``   — ``agent.mcp.servers``: the agent's own pool, the only path that puts
      MCP tools in front of the model. (Nothing connects that pool at boot today.)
    * ``clients`` / ``plugin`` — ``mcp.clients`` and plugin ``.mcp.json``: declared, but
      only ``MCPClientManager.start()`` reads them and nothing calls it.
    * ``cli`` — ``~/.navig/mcp/servers.json``, the ``navig mcp`` CLI's own store.

    ``live`` is ``connected`` / ``disconnected`` / ``not_loaded`` (the gateway holds no
    client for it) or ``unknown`` (no gateway to ask). ``agent_tools`` is how many of its
    tools the agent can actually call — 0 for a server that is connected but not bridged.
    """
    from navig.config import get_config_manager
    from navig.core.coerce import coerce_bool

    cfg = get_config_manager().global_config or {}
    mcp_cfg = cfg.get("mcp") if isinstance(cfg.get("mcp"), dict) else {}
    agent_cfg = (cfg.get("agent") or {}).get("mcp") if isinstance(cfg.get("agent"), dict) else {}
    agent_counts = _agent_tool_counts()

    def _live(name: str) -> str:
        if live_clients is None:
            return "unknown"
        client = live_clients.get(name)
        if client is None:
            return "not_loaded"
        return "connected" if getattr(client, "is_connected", False) else "disconnected"

    def _tools(name: str) -> int | None:
        client = (live_clients or {}).get(name)
        if client is None or not getattr(client, "is_connected", False):
            return None
        return len(getattr(client, "tools", []) or [])

    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(source: str, name: str, entry: dict, *, editable: bool, path: str) -> None:
        if (source, name) in seen:
            return
        seen.add((source, name))
        items.append({
            "name": name,
            "source": source,
            "editable": editable,
            "path": path,
            "transport": str(entry.get("type") or entry.get("transport") or
                             ("http" if entry.get("url") else "stdio")),
            "command": entry.get("command") if isinstance(entry.get("command"), str)
            else " ".join(entry.get("command") or []) if isinstance(entry.get("command"), list)
            else "",
            "args": list(entry.get("args") or []) if isinstance(entry.get("args"), list) else [],
            "url": entry.get("url") or "",
            "enabled": coerce_bool(entry.get("enabled"), default=True),
            "live": _live(name) if source == "servers" else "not_loaded",
            "tools": _tools(name) if source == "servers" else None,
            "agent_tools": agent_counts.get(name, 0),
            # header VALUES are typically credentials — never sent to the UI
            "header_names": sorted((entry.get("headers") or {}).keys())
            if isinstance(entry.get("headers"), dict) else [],
            "env_names": sorted((entry.get("env") or {}).keys())
            if isinstance(entry.get("env"), dict) else [],
        })

    for entry in mcp_cfg.get("servers") or [] if isinstance(mcp_cfg.get("servers"), list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            _add("servers", entry["name"], entry, editable=True, path="config: mcp.servers")
    agent_servers = (agent_cfg or {}).get("servers") if isinstance(agent_cfg, dict) else None
    for entry in agent_servers if isinstance(agent_servers, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            _add("agent", entry["name"], entry, editable=False, path="config: agent.mcp.servers")
    clients = mcp_cfg.get("clients")
    for name, entry in (clients.items() if isinstance(clients, dict) else []):
        if isinstance(entry, dict):
            _add("clients", str(name), entry, editable=False, path=f"config: mcp.clients.{name}")
    try:
        from navig.plugins.package import installed_plugin_roots, load_package

        for root in installed_plugin_roots():
            pkg = load_package(root)
            for name, entry in (pkg.mcp_servers or {}).items():
                if isinstance(entry, dict):
                    _add("plugin", str(name), entry, editable=False,
                         path=f"plugin {pkg.plugin_id}: .mcp.json")
    except Exception as exc:  # noqa: BLE001 — one bad plugin never hides the rest
        logger.debug("customize: plugin MCP scan skipped: %s", exc)
    try:
        from navig.mcp_manager import MCPManager

        for server in MCPManager().list_servers():
            conf = server.config if isinstance(server.config, dict) else {}
            _add("cli", server.name, conf, editable=False, path="~/.navig/mcp/servers.json")
    except Exception as exc:  # noqa: BLE001
        logger.debug("customize: navig mcp CLI store skipped: %s", exc)

    return {"items": items, "gateway": live_clients is not None, "error": None}


# ── Tools ──────────────────────────────────────────────────────────────────────


def _ensure_tools_registered() -> None:
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    if len(_AGENT_REGISTRY) == 0:
        from navig.agent.tools import register_all_tools

        register_all_tools()


def _load_tools() -> dict[str, Any]:
    """The agent's callable tools, with the operator's block policy applied per row.

    `module_enabled` applies the SAME predicate the agent applies before offering a
    tool to the model (`navig.agent.conv.agent` → `disabled_surfaces("toolset")`).
    Without it this surface listed tools the agent would never run: turn a module off
    and the model stops seeing its tools while the OS still showed them as present,
    so a capability could be visibly "on" in the UI and inert everywhere that matters.

    A registry failure is not swallowed here. It propagates to `_section`, which
    reports ``{"items": [], "error": …}`` — an empty list rather than a list in which
    every tool reads as enabled. A gate that opens on error is not a gate.
    """
    from navig.agent.agent_tool_registry import (
        _AGENT_REGISTRY,
        _is_available,
        _operator_block_filter,
    )
    from navig.modules.registry import get_registry

    _ensure_tools_registered()
    # Read the block policy BEFORE touching the module registry: `discover()` walks
    # config, and reading it first keeps this the same order the endpoint had before
    # module gating was added — so adding a gate cannot change what the block policy
    # sees.
    is_blocked = _operator_block_filter()
    module_off = get_registry().disabled_surfaces("toolset")
    items = []
    for entry in _AGENT_REGISTRY.entries():
        schema = entry.schema if isinstance(entry.schema, dict) else {}
        items.append({
            "name": entry.name,
            "toolset": entry.toolset,
            "origin": entry.origin,
            "description": str(schema.get("description") or "")[:300],
            "available": _is_available(entry),
            "blocked": is_blocked(entry.name),
            "module_enabled": entry.toolset not in module_off,
        })
    items.sort(key=lambda t: (t["toolset"], t["name"]))
    return {"items": items, "error": None}


# ── Handlers ───────────────────────────────────────────────────────────────────


def _live_clients(request: "web.Request") -> dict | None:
    from navig.gateway.deck.routes.connectors import live_mcp_manager

    mgr = live_mcp_manager(request)
    return dict(mgr.clients) if mgr is not None else None


async def handle_customize_overview(request: "web.Request") -> "web.Response":
    from navig.gateway.deck.routes.skills import load_skills

    live = _live_clients(request)

    def _read() -> dict[str, Any]:
        skills, skills_err = load_skills()
        sections = {
            "agents": _section(_load_agents),
            "instructions": _section(_load_instructions),
            "hooks": _section(_load_hooks),
            "mcp": _section(lambda: _load_mcp(live)),
            "tools": _section(_load_tools),
        }
        out = {
            key: {"count": len(val.get("items", [])), "error": val.get("error")}
            for key, val in sections.items()
        }
        out["instructions"]["count"] = sum(
            1 for i in sections["instructions"].get("items", []) if i.get("exists")
        )
        out["skills"] = {"count": len(skills), "error": skills_err}
        return out

    return _ok(await asyncio.to_thread(_read))


async def handle_customize_agents(request: "web.Request") -> "web.Response":
    return _ok(await asyncio.to_thread(_section, _load_agents))


async def handle_customize_instructions(request: "web.Request") -> "web.Response":
    return _ok(await asyncio.to_thread(_section, _load_instructions))


def _instruction_name(request: "web.Request") -> str | None:
    name = request.match_info.get("name", "")
    return name if name in INSTRUCTION_FILES else None


async def handle_customize_instruction_get(request: "web.Request") -> "web.Response":
    name = _instruction_name(request)
    if name is None:
        return _err(f"unknown instruction file (allowed: {', '.join(INSTRUCTION_FILES)})", 404)
    path = _resolve_instruction(name)
    if not path.is_file():
        return _ok({"name": name, "path": str(path), "exists": False, "content": "",
                    "mtime_ns": None})
    try:
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _err(f"could not read {name}: {exc}", 500)
    return _ok({"name": name, "path": str(path), "exists": True, "content": content,
                "mtime_ns": path.stat().st_mtime_ns})


async def handle_customize_instruction_save(request: "web.Request") -> "web.Response":
    """Save one workspace file. Atomic, keeps a ``.bak``, refuses a lost update.

    ``expected_mtime_ns`` is the version the editor loaded; if the file changed on disk
    since (another editor, the agent itself appending to MEMORY.md), the save is refused
    with 409 instead of silently overwriting that change.
    """
    name = _instruction_name(request)
    if name is None:
        return _err(f"unknown instruction file (allowed: {', '.join(INSTRUCTION_FILES)})", 404)
    body = await _json_object(request)
    if body is None or not isinstance(body.get("content"), str):
        return _err("body must be {\"content\": string}")
    content: str = body["content"]
    if len(content.encode("utf-8")) > _MAX_INSTRUCTION_BYTES:
        return _err(f"content exceeds {_MAX_INSTRUCTION_BYTES // 1024} KiB", 413)
    expected = body.get("expected_mtime_ns")
    if expected is not None and not isinstance(expected, int):
        return _err("expected_mtime_ns must be an integer or null")

    path = _resolve_instruction(name)

    def _write() -> dict[str, Any] | None:
        from navig.core.yaml_io import atomic_write_text

        current = path.stat().st_mtime_ns if path.is_file() else None
        if expected is not None and current != expected:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        if current is not None:
            atomic_write_text(path.with_name(path.name + ".bak"),
                              path.read_text(encoding="utf-8"))
        atomic_write_text(path, content)
        return {"name": name, "path": str(path), "mtime_ns": path.stat().st_mtime_ns,
                "backup": str(path.with_name(path.name + ".bak")) if current else None}

    try:
        saved = await asyncio.to_thread(_write)
    except (OSError, UnicodeDecodeError) as exc:
        return _err(f"could not save {name}: {exc}", 500)
    if saved is None:
        return _err(f"{name} changed on disk since it was loaded — reload before saving", 409)
    return _ok(saved)


async def handle_customize_hooks(request: "web.Request") -> "web.Response":
    return _ok(await asyncio.to_thread(_section, _load_hooks))


async def handle_customize_mcp(request: "web.Request") -> "web.Response":
    live = _live_clients(request)
    return _ok(await asyncio.to_thread(_section, lambda: _load_mcp(live)))


async def handle_customize_mcp_patch(request: "web.Request") -> "web.Response":
    """Enable/disable one ``mcp.servers`` entry, applied live (the only editable source)."""
    from navig.config import get_config_manager
    from navig.gateway.deck.routes.connectors import apply_mcp_entry_live

    name = request.match_info.get("name", "")
    body = await _json_object(request)
    if body is None or not isinstance(body.get("enabled"), bool):
        return _err("body must be {\"enabled\": boolean}")
    cm = get_config_manager()
    servers = list(((cm.refresh_global_config().get("mcp") or {}).get("servers")) or [])
    idx = next((i for i, s in enumerate(servers)
                if isinstance(s, dict) and s.get("name") == name), None)
    if idx is None:
        return _err(f"no mcp.servers entry named {name!r} (only mcp.servers is editable)", 404)
    servers[idx] = {**servers[idx], "enabled": body["enabled"]}
    cm.set_global("mcp.servers", servers)
    live = await apply_mcp_entry_live(request, name, servers[idx])
    return _ok({"name": name, "enabled": body["enabled"], "live": live})


async def _mcp_connect_toggle(request: "web.Request", connect: bool) -> "web.Response":
    from navig.gateway.deck.routes.connectors import live_mcp_manager

    name = request.match_info.get("name", "")
    mgr = live_mcp_manager(request)
    if mgr is None:
        return _err("no running gateway to connect through", 503)
    if name not in mgr.clients:
        return _err(f"{name!r} is not loaded by the gateway (enable it first)", 409)
    try:
        if connect:
            ok = await mgr.connect_client(name)
        else:
            ok = await mgr.disconnect_client(name)
    except Exception as exc:  # noqa: BLE001 — a failed connect is a result, not a 500
        return _ok({"name": name, "connected": False, "error": str(exc)})
    client = mgr.clients.get(name)
    return _ok({"name": name, "connected": bool(client and client.is_connected),
                "ok": bool(ok)})


async def handle_customize_mcp_connect(request: "web.Request") -> "web.Response":
    return await _mcp_connect_toggle(request, True)


async def handle_customize_mcp_disconnect(request: "web.Request") -> "web.Response":
    return await _mcp_connect_toggle(request, False)


async def handle_customize_tools(request: "web.Request") -> "web.Response":
    return _ok(await asyncio.to_thread(_section, _load_tools))


async def handle_customize_tool_patch(request: "web.Request") -> "web.Response":
    """Block/unblock a tool via ``tools.blocked_tools`` — the policy dispatch enforces."""
    from navig.config import get_config_manager
    from navig.tools.router import canonical_tool_key

    name = request.match_info.get("name", "")
    body = await _json_object(request)
    if body is None or not isinstance(body.get("blocked"), bool):
        return _err("body must be {\"blocked\": boolean}")
    key = canonical_tool_key(name)
    if not key:
        return _err("invalid tool name")
    cm = get_config_manager()
    current = ((cm.refresh_global_config().get("tools") or {}).get("blocked_tools")) or []
    current = [str(n) for n in current] if isinstance(current, list) else []
    # Compare canonically (the policy is read canonically), keep the operator's spellings.
    kept = [n for n in current if canonical_tool_key(n) != key]
    updated = kept + [name] if body["blocked"] else kept
    cm.set_global("tools.blocked_tools", updated)
    return _ok({"name": name, "blocked": body["blocked"], "blocked_tools": updated})
