"""The Store aggregator — every connectable thing, one list, one wire state.

Composes (reuses, never reimplements):
- module registry  (`navig.modules.registry`)  → system plugins + launchers
- plugin host      (`navig.plugins.host`)      → installed plugins, all formats
- skills loader    (`navig.skills.loader`)     → skills (grouped by provider)
- MCP config + plugin `.mcp.json`              → MCP servers
- connector registry (`navig.connectors`)      → service connectors
- command-provider map (`navig.cli.providers`) → AVAILABLE (known, not installed)

User-facing display model: ONE noun (plugin) + badges (`system`, `standalone`,
`locked`); `kind` is internal grouping only. Consumed by `navig store` (CLI)
and `GET /api/deck/store` — both call :func:`collect_store` /
:func:`apply_action`.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class WireState(str, Enum):
    WIRED = "wired"          # installed + enabled + usable (healthy or degraded)
    UNWIRED = "unwired"      # installed but disabled / disconnected / tier-locked
    AVAILABLE = "available"  # known from catalog/command-map, not installed
    BROKEN = "broken"        # installed but FAILED / failing health check


@dataclass
class StoreItem:
    id: str                  # "<kind>:<name>" — globally unique
    kind: str                # module | plugin | skill | mcp | connector
    label: str
    description: str = ""
    state: str = WireState.WIRED.value
    degraded: bool = False
    locked: bool = False
    system: bool = False
    standalone: bool = False
    provider: str | None = None    # owning plugin id, if any
    source: str = ""
    version: str = ""
    actions: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_STANDALONE_MODULE_IDS = {"navig-menu", "vault", "navig-vault", "mini", "navig-mini"}


def collect_store(*, include_available: bool = True, refresh: bool = False) -> list[StoreItem]:
    """Aggregate every connectable thing. Each collector is best-effort — a
    broken subsystem yields a warning, never an exception."""
    items: list[StoreItem] = []
    for collector in (_modules, _plugins, _skills, _mcp_servers, _connectors):
        try:
            items.extend(collector())
        except Exception as exc:  # noqa: BLE001
            logger.warning("store collector %s failed: %s", collector.__name__, exc)
    if include_available:
        try:
            installed_plugin_names = {
                i.id.split(":", 1)[1] for i in items if i.kind == "plugin"
            }
            items.extend(_available(installed_plugin_names, refresh=refresh))
        except Exception as exc:  # noqa: BLE001
            logger.warning("store available-catalog failed: %s", exc)
        try:
            items.extend(_bay_apps(refresh=refresh))
        except Exception as exc:  # noqa: BLE001
            logger.warning("store bay-apps failed: %s", exc)
    return items


def store_status(items: list[StoreItem] | None = None) -> dict[str, Any]:
    """One-screen summary: counts per kind/state + what's broken/degraded.

    Pass already-collected *items* to avoid a second full aggregation sweep
    (the summary is a pure fold; AVAILABLE rows are excluded either way).
    """
    if items is not None:
        items = [i for i in items if i.state != WireState.AVAILABLE.value]
    else:
        items = collect_store(include_available=False)
    by_state: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    broken: list[dict[str, Any]] = []
    degraded: list[str] = []
    for item in items:
        by_state[item.state] = by_state.get(item.state, 0) + 1
        by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
        if item.state == WireState.BROKEN.value:
            broken.append({"id": item.id, "error": item.detail.get("error", "")})
        elif item.degraded:
            degraded.append(item.id)
    return {
        "total": len(items),
        "by_state": by_state,
        "by_kind": by_kind,
        "broken": broken,
        "degraded": degraded,
    }


def apply_action(item_id: str, action: str) -> dict[str, Any]:
    """Kind-dispatched action (enable/disable/install/remove). Shared by the
    CLI and the deck POST route. Returns {"ok": bool, "message": str, ...}."""
    kind, _, name = item_id.partition(":")
    if not name:
        # bare name — try plugin first, then module
        kind, name = "", item_id

    if action in {"wire", "unwire"}:  # hidden aliases
        action = "enable" if action == "wire" else "disable"

    try:
        if kind in {"module", ""} and action in {"enable", "disable"}:
            from navig.modules.registry import get_registry

            registry = get_registry()
            if kind == "module" or any(m["id"] == name for m in registry.list_modules()):
                if not registry.set_enabled(name, action == "enable"):
                    return {"ok": False, "message": f"Unknown module '{name}'"}
                return {"ok": True, "message": f"Module '{name}' {action}d"}

        if kind in {"plugin", ""}:
            from navig.plugins.host import get_plugin_host

            host = get_plugin_host()
            if action == "enable":
                host.enable(name)
                return {"ok": True, "message": f"Plugin '{name}' enabled"}
            if action == "disable":
                host.disable(name)
                return {"ok": True, "message": f"Plugin '{name}' disabled"}
            if action == "install":
                # Host sources (dir/zip/git/marketplace) first, then the pip
                # distribution from the command-provider map — the AVAILABLE
                # rows this hub advertises are pip-distributed.
                from navig.cli.providers import install_plugin_by_name

                return {"ok": True, "message": install_plugin_by_name(name)}
            if action == "remove":
                host.uninstall(name)
                return {"ok": True, "message": f"Removed plugin '{name}'"}

        if kind in {"webapp", "app"} and action in {"open", "install", "unlock"}:
            # Bay webapps/apps: the URL/checkout is resolved from the catalog and
            # returned; the client (deck/os) or the CLI opens it.
            item = next((i for i in collect_store() if i.id == item_id), None)
            if item is None:
                return {"ok": False, "message": f"'{item_id}' not found in the Bay"}
            if action == "unlock" or item.locked:
                bay_id = item.detail.get("bayId", name)
                return {"ok": True, "message": "Opening Harbor checkout",
                        "url": f"https://api.navig.run/api/checkout?item={bay_id}"}
            url = item.detail.get("url", "")
            return {"ok": True, "message": f"Open {item.label}", "url": url}

        if kind == "connector":
            return {
                "ok": False,
                "message": f"Connectors use their own auth flow: navig connector connect {name}",
            }
        if kind == "mcp":
            return {
                "ok": False,
                "message": f"Toggle MCP servers in config (mcp.clients.{name}.enabled) or `navig mcp`",
            }
    except KeyError as exc:
        return {"ok": False, "message": str(exc.args[0]) if exc.args else str(exc)}
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": False, "message": f"Unsupported action '{action}' for '{item_id}'"}


# ── collectors ────────────────────────────────────────────────────────────────


def _modules() -> list[StoreItem]:
    from navig.modules.registry import get_registry

    items: list[StoreItem] = []
    for m in get_registry().list_modules():
        mid = str(m.get("id", ""))
        if not mid or mid.startswith("plugin:"):
            continue  # installed CC plugins are covered by the plugin collector
        locked = bool(m.get("locked"))
        enabled = bool(m.get("enabled"))
        state = WireState.WIRED if enabled and not locked else WireState.UNWIRED
        is_primitive = str(m.get("kind", "")) == "primitive"
        actions: list[str] = []
        if not locked and not is_primitive:
            actions.append("disable" if enabled else "enable")
        items.append(StoreItem(
            id=f"module:{mid}",
            kind="module",
            label=str(m.get("label") or mid),
            description=str(m.get("description") or ""),
            state=state.value,
            locked=locked,
            system=str(m.get("source", "builtin")) == "builtin",
            standalone=str(m.get("kind", "")) == "launcher" or mid in _STANDALONE_MODULE_IDS,
            source=str(m.get("source", "builtin")),
            actions=actions,
            detail={"kind": m.get("kind"), "category": m.get("category"),
                    "capability": m.get("capability"), "icon": m.get("icon")},
        ))
    return items


def _plugins() -> list[StoreItem]:
    from navig.plugins.host import get_plugin_host

    items: list[StoreItem] = []
    for p in get_plugin_host().list_installed():
        health_state = p.health.state.value if p.health is not None else "healthy"
        if health_state == "failed":
            state = WireState.BROKEN
        elif not p.enabled:
            state = WireState.UNWIRED
        else:
            state = WireState.WIRED
        actions = ["disable" if p.enabled else "enable"]
        if p.source != "builtin" and p.format != "pip":
            actions.append("remove")
        items.append(StoreItem(
            id=f"plugin:{p.id}",
            kind="plugin",
            label=p.id,
            description=p.description,
            state=state.value,
            degraded=health_state == "degraded",
            standalone=p.id in _STANDALONE_MODULE_IDS,
            source=p.source,
            version=p.version,
            actions=actions,
            detail={
                "format": p.format,
                "path": str(p.path) if p.path else None,
                "commands": sorted(p.commands),
                "error": (p.health.error or "") if p.health is not None else "",
            },
        ))
    return items


def _skills() -> list[StoreItem]:
    from navig.plugins.package import installed_plugin_roots
    from navig.skills.loader import load_all_skills

    # Resolve each root ONCE, not per-skill (root.resolve() is loop-invariant).
    resolved_roots = {root.name: root.resolve() for root in installed_plugin_roots()}
    items: list[StoreItem] = []
    for skill in load_all_skills():
        sid = getattr(skill, "id", None) or getattr(skill, "name", None)
        if not sid:
            continue
        path = getattr(skill, "source_path", None)  # Skill dataclass field name
        provider = None
        if path is not None:
            try:
                skill_path = Path(path).resolve()  # once per skill
                for name, root in resolved_roots.items():
                    if skill_path.is_relative_to(root):
                        provider = name
                        break
            except Exception:  # noqa: BLE001
                pass
        items.append(StoreItem(
            id=f"skill:{sid}",
            kind="skill",
            label=str(getattr(skill, "name", sid)),
            description=str(getattr(skill, "description", "") or "")[:120],
            state=WireState.WIRED.value,
            provider=provider,
            source="plugin" if provider else "local",
            detail={"path": str(path) if path else None},
        ))
    return items


def _mcp_servers() -> list[StoreItem]:
    from navig.config import get_config_manager
    from navig.plugins.package import installed_plugin_roots, load_package

    items: list[StoreItem] = []
    seen: set[str] = set()

    cfg = get_config_manager().global_config or {}
    clients = cfg.get("mcp", {}).get("clients", {}) if isinstance(cfg, dict) else {}
    if isinstance(clients, dict):
        for name, client_cfg in clients.items():
            enabled = bool(client_cfg.get("enabled", True)) if isinstance(client_cfg, dict) else True
            seen.add(name)
            items.append(StoreItem(
                id=f"mcp:{name}",
                kind="mcp",
                label=name,
                description="MCP server (config)",
                state=(WireState.WIRED if enabled else WireState.UNWIRED).value,
                source="config",
            ))
    for root in installed_plugin_roots():
        pkg = load_package(root)
        for name in (pkg.mcp_servers or {}):
            if name in seen:
                continue
            seen.add(name)
            items.append(StoreItem(
                id=f"mcp:{name}",
                kind="mcp",
                label=name,
                description=f"MCP server (plugin {pkg.plugin_id})",
                state=WireState.WIRED.value,
                provider=pkg.plugin_id,
                source="plugin",
            ))
    return items


def _connectors() -> list[StoreItem]:
    from navig.connectors.registry import get_connector_registry

    items: list[StoreItem] = []
    for c in get_connector_registry().list_all():
        cid = str(c.get("id", ""))
        if not cid:
            continue
        status = str(c.get("status", "disconnected"))
        if status in {"connected", "degraded"}:
            state = WireState.WIRED
        elif status in {"error", "failed"}:
            state = WireState.BROKEN
        else:
            state = WireState.UNWIRED
        items.append(StoreItem(
            id=f"connector:{cid}",
            kind="connector",
            label=str(c.get("name") or cid),
            description=str(c.get("description") or ""),
            state=state.value,
            degraded=status == "degraded",
            source=str(c.get("domain", "")),
            detail={"status": status},
        ))
    return items


def _available(installed_plugin_names: set[str], *, refresh: bool = False) -> list[StoreItem]:
    """Known-but-not-installed plugins from the command-provider map (and, with
    refresh, registered marketplaces)."""
    items: list[StoreItem] = []
    seen: set[str] = set(installed_plugin_names)

    try:
        from navig.cli.providers import known_providers

        for plugin_name, info in known_providers().items():
            if plugin_name in seen:
                continue
            seen.add(plugin_name)
            items.append(StoreItem(
                id=f"plugin:{plugin_name}",
                kind="plugin",
                label=plugin_name,
                description=str(info.get("description", "") or ""),
                state=WireState.AVAILABLE.value,
                source="catalog",
                actions=["install"],
                detail={"commands": info.get("commands", []), "pip": info.get("pip")},
            ))
    except Exception as exc:  # noqa: BLE001
        logger.debug("command-provider catalog unavailable: %s", exc)

    if refresh:
        try:
            from navig.plugins.marketplace import MarketplaceStore, fetch_marketplace

            for row in MarketplaceStore().list_marketplaces():
                try:
                    mkt = fetch_marketplace(row.url)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("marketplace %s unreachable: %s", row.name, exc)
                    continue
                for entry in mkt.entries:
                    if entry.name in seen:
                        continue
                    seen.add(entry.name)
                    items.append(StoreItem(
                        id=f"plugin:{entry.name}",
                        kind="plugin",
                        label=entry.name,
                        description=entry.description or "",
                        state=WireState.AVAILABLE.value,
                        source=f"marketplace:{row.name}",
                        version=entry.version or "",
                        actions=["install"],
                    ))
        except Exception as exc:  # noqa: BLE001
            logger.debug("marketplace refresh failed: %s", exc)
    return items


def _price_cents(pricing: dict[str, Any]) -> int | None:
    """Cents for a Bay entry's nested pricing block (``priceUsd`` → cents, or ``priceCents``)."""
    usd = pricing.get("priceUsd")
    if isinstance(usd, (int, float)):
        return int(round(usd * 100))
    cents = pricing.get("priceCents")
    return int(cents) if isinstance(cents, (int, float)) else None


def _bay_apps(*, refresh: bool = False) -> list[StoreItem]:
    """Surface the cloud Bay's `webapp` / `app` entries (Photopea, Design Mode…)
    so the Store shows them alongside plugins/modules. Entitlement-gated through the
    ONE canonical rule (navig.license.entitlement) — free/owned/tier-covered → `available`
    (open); a priced entry you can't unlock → `unwired` (locked → unlock). Fails OPEN so a
    licensing hiccup never hides/locks the catalog. Offline-safe via the cached catalog."""
    from navig.hub.bay import fetch_bay_catalog
    from navig.license.entitlement import capability_for, is_entry_unlocked, live_caps_and_rank

    try:
        caps, rank = live_caps_and_rank()
        license_ok = True
    except Exception:  # noqa: BLE001 — license down → fail OPEN (do not lock the catalog)
        caps, rank, license_ok = [], 0, False

    items: list[StoreItem] = []
    for entry in fetch_bay_catalog(refresh=refresh):
        kind = str(entry.get("kind", ""))
        if kind not in ("webapp", "app"):
            continue
        name = str(entry.get("name", ""))
        if not name:
            continue
        slug = str(entry.get("slug") or name)
        # Nested `pricing` block (camelCase) — NOT the flat `price_cents` the catalog never
        # carried, which made every Bay app resolve as free/unlocked (dead gate).
        pricing = entry.get("pricing") if isinstance(entry.get("pricing"), dict) else {}
        cap = capability_for(pricing, slug)
        entitled = True if not license_ok else is_entry_unlocked(
            pricing, slug, caps=caps, tier_rank=rank
        )
        url = str(entry.get("url") or entry.get("source") or "")
        price = _price_cents(pricing)
        items.append(StoreItem(
            id=f"{kind}:{name}",
            kind=kind,
            label=name,
            description=str(entry.get("description", "") or ""),
            state=(WireState.AVAILABLE if entitled else WireState.UNWIRED).value,
            locked=not entitled,
            standalone=True,
            source="bay",
            version=str(entry.get("version", "") or ""),
            actions=["open"] if entitled else ["unlock"],
            detail={
                "url": url,
                "delivery": "webapp" if kind == "webapp" else "browser-app",
                "bayId": name,
                "price_cents": price,
                "capability": cap,
            },
        ))
    return items
