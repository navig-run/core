"""Deck catalog + spaces-registry routes.

Powers the navig-deck "Spaces" control panel + the catalog/marketplace:

    GET  /api/deck/spaces/scan            → discovered spaces (manifest + progress + enabled + active)
    GET  /api/deck/catalog                → installed + available (spaces · personas · packages)
    POST /api/deck/spaces/{id}/enable      → show in deck/switcher
    POST /api/deck/spaces/{id}/disable     → hide (folder still works when you're in it)
    POST /api/deck/spaces/{id}/activate    → set the active space (binds the working dir)
    POST /api/deck/spaces/register         → register an external .navig/ folder (enabled)

All registry state lives in ~/.navig/spaces.json (see navig.spaces.registry); discovery +
manifest parsing reuse navig.spaces.{resolver,space_manifest}. Read-only progress reuses the
ROADMAP parser from routes.apps.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _ok(data: Any) -> "web.Response":
    return web.json_response({"ok": True, "data": data})


def _err(msg: str, status: int = 400) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


def _active_path() -> str | None:
    try:
        from navig.spaces import registry as _registry  # noqa: PLC0415

        active = _registry.load_registry().get("active")
        if active:
            return _registry._norm(active)
        from navig.spaces.active import get_active_working_dir  # noqa: PLC0415

        return _registry._norm(get_active_working_dir())
    except Exception:  # noqa: BLE001
        return None


def _space_card(canonical: str, cfg: Any, *, active_path: str | None) -> dict[str, Any]:
    """Build a deck card for a discovered space (manifest + progress + flags)."""
    from navig.gateway.deck.routes.apps import _parse_roadmap_milestones  # noqa: PLC0415
    from navig.spaces import registry as _registry  # noqa: PLC0415
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    path = Path(cfg.path)
    manifest = load_space_manifest(path)
    milestones = _parse_roadmap_milestones(path / "ROADMAP.md")
    if not milestones:  # federated spaces keep plans under .navig/plans/
        milestones = _parse_roadmap_milestones(path / ".navig" / "plans" / "ROADMAP.md")
    total = len(milestones)
    done = sum(1 for m in milestones if m.get("done"))
    next_action = next((m["title"] for m in milestones if not m.get("done")), None)
    rp = _registry._norm(str(path))
    return {
        "id": canonical,
        "name": manifest.resolved_name or canonical,
        "path": str(path),
        "scope": cfg.scope,
        "tier": manifest.get("tier") or manifest.get("type"),
        "status": manifest.get("status"),
        "description": manifest.get("description") or manifest.get("tagline") or "",
        "enabled": _registry.is_enabled(path),
        "active": active_path is not None and rp == active_path,
        "completion_pct": int(done / total * 100) if total else 0,
        "milestones_total": total,
        "milestones_done": done,
        "next_action": next_action,
        "counts": {
            "skills": len(manifest.skill_allowlist),
            "packages": len(manifest.package_allowlist),
            "personas": len(manifest.get("personas") or []) if isinstance(manifest.get("personas"), list) else 0,
            "formations": 1 if manifest.resolved_formation else 0,
        },
    }


async def handle_deck_spaces_scan(request: "web.Request") -> "web.Response":
    """GET — every discovered space (across roots), enriched + flagged."""
    try:
        from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415

        active = _active_path()
        spaces = discover_space_paths(include_disabled=True)
        cards = [_space_card(name, cfg, active_path=active) for name, cfg in sorted(spaces.items())]
        # active first, then enabled, then name
        cards.sort(key=lambda c: (0 if c["active"] else 1, 0 if c["enabled"] else 1, c["name"]))
        return _ok({"spaces": cards, "active": next((c["id"] for c in cards if c["active"]), None)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("spaces/scan failed")
        return _err(str(exc), 500)


def _community_root() -> Path | None:
    """Best-effort locate the navig-community registry (dev repo or ~/.navig)."""
    candidates: list[Path] = [Path.home() / ".navig" / "community"]
    # Walk up from this file looking for a sibling `navig-community` (repo layout,
    # regardless of how deep the package is installed).
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / "navig-community")
    for c in candidates:
        if (c / "community.yaml").is_file() or (c / "spaces" / "registry.json").is_file():
            return c
    return None


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))  # tolerate a BOM
    except Exception:  # noqa: BLE001
        return None


# The community marketplace lives in the navig-run/community GitHub repo.
_MARKET_OWNER = "navig-run"
_MARKET_REPO = "community"


def _titleize(s: str) -> str:
    return s.replace("-space", "").replace("-", " ").replace("_", " ").strip().title()


def _market_card(kind: str, raw: dict[str, Any], *, installed: bool) -> dict[str, Any]:
    """Normalize a community registry entry into a marketplace card."""
    cid = str(raw.get("id") or raw.get("name") or "unknown")
    pillar = {"space": "spaces", "persona": "personas", "package": "packages"}[kind]
    market = raw.get("marketplace") if isinstance(raw.get("marketplace"), dict) else {}
    return {
        "kind": kind,
        "id": cid,
        "name": str(raw.get("display_name") or raw.get("name") or _titleize(cid)),
        "description": str(raw.get("tagline") or raw.get("description") or ""),
        "tier": raw.get("tier"),
        "status": raw.get("status"),
        "sub_spaces": raw.get("sub_spaces") or [],
        "vendor": (market.get("vendor") if market else None) or "community",
        "price": (str(market["price"]) if market and market.get("price") is not None else None),
        "currency": (market.get("currency") if market else None),
        "spec": f"github:{_MARKET_OWNER}/{_MARKET_REPO}/{pillar}/{cid}",
        "installed": installed,
    }


async def handle_deck_catalog(request: "web.Request") -> "web.Response":
    """GET — the marketplace: installed registry + normalized available items.

    Each available item is a marketplace card (name · tagline · tier · price ·
    vendor · install spec · installed flag) the deck renders with an Install button.
    """
    try:
        from navig.platform.paths import config_dir  # noqa: PLC0415
        from navig.spaces import registry as _registry  # noqa: PLC0415

        installed = _registry.load_registry().get("spaces", [])
        installed_space_ids = {str(e.get("id")) for e in installed}

        available: dict[str, list] = {"spaces": [], "personas": [], "packages": []}
        tiers: dict[str, str] = {}
        root = _community_root()
        if root is not None:
            reg = _read_json(root / "spaces" / "registry.json")
            if isinstance(reg, dict):
                if isinstance(reg.get("tiers"), dict):
                    tiers = {str(k): str(v) for k, v in reg["tiers"].items()}
                for sp in reg.get("spaces", []) if isinstance(reg.get("spaces"), list) else []:
                    if isinstance(sp, dict):
                        available["spaces"].append(
                            _market_card("space", sp, installed=str(sp.get("id")) in installed_space_ids)
                        )
            personas_dir = root / "personas"
            if personas_dir.is_dir():
                for d in sorted(personas_dir.iterdir()):
                    if d.is_dir():
                        meta = _read_json(d / "persona.json") or {}
                        meta["id"] = d.name
                        is_inst = (config_dir() / "personas" / d.name).is_dir()
                        available["personas"].append(_market_card("persona", meta, installed=is_inst))

        # Installed navig packages (informational — already local).
        try:
            from navig.commands.package import _discover_packages  # type: ignore  # noqa: PLC0415

            for p in (_discover_packages() or [])[:200]:
                pid = getattr(p, "id", None) or (p.get("id") if isinstance(p, dict) else None) or str(p)
                available["packages"].append(_market_card("package", {"id": str(pid)}, installed=True))
        except Exception:  # noqa: BLE001
            pass

        return _ok({"installed": installed, "available": available, "tiers": tiers})
    except Exception as exc:  # noqa: BLE001
        logger.exception("catalog failed")
        return _err(str(exc), 500)


async def handle_deck_catalog_install(request: "web.Request") -> "web.Response":
    """POST { spec, force?, upgrade? } — install a marketplace item via the additive installer."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err("invalid JSON")
    spec = str(body.get("spec") or "").strip()
    if not spec:
        return _err("'spec' is required")
    force = bool(body.get("force"))
    upgrade = bool(body.get("upgrade"))

    import asyncio  # noqa: PLC0415

    from navig.commands.install import install_asset  # noqa: PLC0415

    loop = asyncio.get_event_loop()
    try:
        # install_asset does blocking network/file I/O — keep the event loop free.
        await loop.run_in_executor(None, lambda: install_asset(spec, force=force, upgrade=upgrade))
    except Exception as exc:  # noqa: BLE001
        logger.warning("catalog install failed for %s: %s", spec, exc)
        return _err(f"install failed: {exc}", 400)
    return _ok({"spec": spec, "installed": True})


async def handle_deck_space_enable(request: "web.Request") -> "web.Response":
    from navig.spaces import registry as _registry  # noqa: PLC0415

    sid = request.match_info.get("id", "")
    return _ok({"id": sid, "enabled": True}) if _registry.set_enabled(sid, True) else _err("not registered", 404)


async def handle_deck_space_disable(request: "web.Request") -> "web.Response":
    from navig.spaces import registry as _registry  # noqa: PLC0415

    sid = request.match_info.get("id", "")
    return _ok({"id": sid, "enabled": False}) if _registry.set_enabled(sid, False) else _err("not registered", 404)


async def handle_deck_space_activate(request: "web.Request") -> "web.Response":
    """Set the active space (binds the working dir, like `navig space switch`)."""
    try:
        from navig.spaces import registry as _registry  # noqa: PLC0415
        from navig.spaces.active import set_active_working_dir  # noqa: PLC0415
        from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415
        from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

        sid = request.match_info.get("id", "")
        cfg = discover_space_paths(include_disabled=True).get(sid)
        if cfg is None:
            return _err(f"space '{sid}' not found", 404)
        manifest = load_space_manifest(Path(cfg.path))
        working_dir = (Path(cfg.path) / (manifest.root or ".")).resolve()
        set_active_working_dir(working_dir)
        _registry.mark_active(cfg.path)
        # Mirror the name into config (best-effort) so CLI get_active_space agrees.
        try:
            from navig.commands.space import _set_active_space  # noqa: PLC0415
            _set_active_space(sid)
        except Exception:  # noqa: BLE001
            pass
        return _ok({"id": sid, "active": True, "working_dir": str(working_dir)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("spaces/activate failed")
        return _err(str(exc), 500)


async def handle_deck_space_register(request: "web.Request") -> "web.Response":
    """POST { path } — register an external .navig/ folder (enabled)."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err("invalid JSON")
    raw = (body.get("path") or "").strip()
    if not raw:
        return _err("'path' is required")
    from navig.spaces import registry as _registry  # noqa: PLC0415
    from navig.spaces.contracts import normalize_space_name  # noqa: PLC0415
    from navig.spaces.space_manifest import is_space_dir, load_space_manifest  # noqa: PLC0415

    target = Path(raw).expanduser()
    if not target.is_dir() or not is_space_dir(target):
        return _err("not a space (needs a .navig/ directory)", 400)
    manifest = load_space_manifest(target)
    sid = normalize_space_name(manifest.resolved_id or target.name)
    entry = _registry.register(target, id=sid, name=manifest.resolved_name or target.name, source="external", enabled=True)
    return web.json_response({"ok": True, "data": entry}, status=201)
