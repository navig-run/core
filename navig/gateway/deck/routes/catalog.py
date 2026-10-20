"""Deck catalog + spaces-registry routes.

Powers the navig-deck "Spaces" control panel + the catalog/marketplace:

    GET  /api/deck/spaces/scan            → discovered spaces (manifest + progress + enabled + active)
    GET  /api/deck/catalog                → installed + available (spaces · personas · packages)
    POST /api/deck/spaces/{id}/enable      → show in deck/switcher
    POST /api/deck/spaces/{id}/disable     → hide (folder still works when you're in it)
    POST /api/deck/spaces/{id}/activate    → set the active space (binds the working dir)
    POST /api/deck/spaces/{id}/apps        → set the pinned-apps view-filter (allow-list)
    POST /api/deck/spaces/{id}/books       → set the finance BOOK (separate ledger; null = default)
    POST /api/deck/spaces/{id}/info        → set the display name / description / category (null = clear)
    POST /api/deck/spaces/register         → register an external .navig/ folder (enabled)
    POST /api/deck/spaces/repath           → the folder moved: carry its registry row to the new path
    POST /api/deck/spaces/forget           → drop a space's registry row (folder untouched)

All registry state lives in ~/.navig/spaces.json (see navig.spaces.registry); discovery +
manifest parsing reuse navig.spaces.{resolver,space_manifest}. Read-only progress reuses the
ROADMAP parser from routes.apps.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
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
        # Pinned apps (desktop sidebar view-filter): non-empty = only these, in
        # this order; empty = all. Never affects enablement (that's global).
        "app_allowlist": manifest.app_allowlist,
        # Per-app SECTION layouts (order + hidden) — the same kind of view-filter
        # as app_allowlist, one level down.
        "app_sections": manifest.app_sections,
        "counts": {
            "skills": len(manifest.skill_allowlist),
            "packages": len(manifest.package_allowlist),
            "personas": len(manifest.get("personas") or []) if isinstance(manifest.get("personas"), list) else 0,
            "formations": 1 if manifest.resolved_formation else 0,
            "apps": len(manifest.app_allowlist),
        },
    }


async def handle_deck_spaces_scan(request: "web.Request") -> "web.Response":
    """GET — every discovered space (across roots), enriched + flagged."""

    def _collect() -> dict[str, Any]:
        # Enumerating spaces + loading each manifest + parsing two ROADMAP files per space is
        # all blocking filesystem work — run it off the event loop so a machine with many spaces
        # (or a slow disk) can't stall the gateway. Mirrors handle_deck_spaces (#441).
        from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415

        active = _active_path()
        spaces = discover_space_paths(include_disabled=True)
        cards = [_space_card(name, cfg, active_path=active) for name, cfg in sorted(spaces.items())]
        # active first, then enabled, then name
        cards.sort(key=lambda c: (0 if c["active"] else 1, 0 if c["enabled"] else 1, c["name"]))
        return {"spaces": cards, "active": next((c["id"] for c in cards if c["active"]), None)}

    try:
        return _ok(await asyncio.to_thread(_collect))
    except Exception as exc:  # noqa: BLE001
        logger.exception("spaces/scan failed")
        return _err(str(exc), 500)


def _community_root() -> Path | None:
    """Best-effort locate the registry content root (monorepo registry/ or ~/.navig)."""
    from navig.platform.paths import config_dir

    candidates: list[Path] = [config_dir() / "community"]
    # Walk up from this file, checking in precedence order per parent: a sibling
    # `navig-community` checkout (the pre-2026-10-16 polyrepo name), then a `registry/community` mirror
    # if one was ever synced there, then the in-repo index at `registry/` itself
    # — this monorepo keeps `registry/community.yaml` + `registry/<type>/
    # registry.json` DIRECTLY under `registry/` (no `community/` subdir), so
    # without this last candidate the daemon catalog resolves to nothing here.
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / "navig-community")
        candidates.append(parent / "registry" / "community")
        candidates.append(parent / "registry")
    for c in candidates:
        if (c / "community.yaml").is_file() or (c / "spaces" / "registry.json").is_file():
            return c
    return None


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))  # tolerate a BOM
    except Exception:  # noqa: BLE001
        return None


# The marketplace lives in the first-party registry repo (navig.registry_repo names it).
from navig.registry_repo import OWNER as _MARKET_OWNER  # noqa: E402
from navig.registry_repo import REPO as _MARKET_REPO  # noqa: E402
from navig.registry_repo import normalize_spec as _normalize_registry_spec  # noqa: E402


def _titleize(s: str) -> str:
    return s.replace("-space", "").replace("-", " ").replace("_", " ").strip().title()


# Entitlement primitives now live in navig.license.entitlement (the one canonical home,
# neutral to both this deck route and the hub Store collector). Re-exported under the
# historical private names so callers here — and test_entitlement_parity.py, which imports
# `catalog._TIER_RANK` — keep working unchanged.
from navig.license.entitlement import TIER_RANK as _TIER_RANK  # noqa: E402,F401
from navig.license.entitlement import is_unlocked as _is_unlocked  # noqa: E402
from navig.license.entitlement import live_caps_and_rank as _live_caps_and_rank  # noqa: E402


def _pricing_fields(cid: str, market: dict[str, Any]) -> dict[str, Any]:
    """Derive Harbor Bay pricing + unlock state from an item's marketplace block.

    Models: absent/``free`` → free; ``tier`` → included while the Harbor tier
    covers it; ``buy_once`` → perpetual ``item:<id>`` grant (a covering tier
    ALSO unlocks it while active). ``unlocked`` reflects the CURRENT license.
    """
    model = str(market.get("pricing_model") or "free") if market else "free"
    price_cents = market.get("price_cents") if market else None
    included_in = str(market["included_in_tier"]) if market and market.get("included_in_tier") else None
    capability = f"item:{cid.lower()}" if model == "buy_once" else None

    try:
        caps, tier_rank = _live_caps_and_rank()
        unlocked = _is_unlocked(
            model=model, capability=capability, included_in=included_in,
            caps=caps, tier_rank=tier_rank,
        )
    except Exception:  # noqa: BLE001 — license subsystem down → fail open
        unlocked = True
    return {
        "pricing_model": model,
        "price_cents": int(price_cents) if isinstance(price_cents, (int, float)) else None,
        "included_in_tier": included_in,
        "capability": capability,
        "unlocked": unlocked,
    }


def _market_card(kind: str, raw: dict[str, Any], *, installed: bool) -> dict[str, Any]:
    """Normalize a community registry entry into a Harbor Bay card."""
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
        **_pricing_fields(cid, market),
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

        return _ok({"installed": installed, "available": available, "tiers": tiers})
    except Exception as exc:  # noqa: BLE001
        logger.exception("catalog failed")
        return _err(str(exc), 500)


def _bay_catalog_path() -> Path | None:
    """Locate the generated Harbor Bay catalog (ONE normalizer, many surfaces).

    Preference: the copy shipped inside the package (``navig/data/`` — present
    in an editable dev tree and in the wheel via ``package-data``), then the web
    build output in a full monorepo checkout. The generator
    (``web/www/scripts/build-bay-catalog.mjs``) writes BOTH from the same
    normalization pass, so they never disagree.
    """
    bundled = Path(__file__).resolve().parents[3] / "data" / "bay-catalog.json"
    if bundled.is_file():
        return bundled
    for parent in Path(__file__).resolve().parents:
        cand = parent / "web" / "www" / "content" / "bay-catalog.generated.json"
        if cand.is_file():
            return cand
    return None


def _installed_space_ids() -> set[str]:
    """Every name an installed space answers to, for the catalog ``installed`` flag.

    A registry entry's ``id`` is often NOT the catalog slug: the Bay installs ``system-space``
    under that id, but a space registered any other way carries its normalized or federated
    id (``homelab-system``, ``human``) while its folder keeps the slug (``system-space``).
    Matching on the id alone reported every such space as not installed.
    """
    try:
        from navig.spaces import registry as _registry  # noqa: PLC0415
        from navig.spaces.contracts import normalize_space_name  # noqa: PLC0415

        names: set[str] = set()
        for e in _registry.load_registry().get("spaces", []):
            for value in (e.get("id"), Path(str(e.get("path") or "")).name):
                if value:
                    names.add(str(value))
                    names.add(normalize_space_name(str(value)))
        return names
    except Exception:  # noqa: BLE001
        return set()


def _installed_plugin_ids() -> set[str]:
    """Ids of plugins installed locally, in both forms a catalog slug may take.

    The plugin host keys a pip plugin by its distribution name (``navig-audio``) while
    ``_plugin_id_of`` strips catalog slugs to the bare id (``audio``), so comparing the two
    never matched: every installed pip plugin showed "Install". Both forms are in the set.
    """
    try:
        from navig.plugins.host import get_plugin_host  # noqa: PLC0415

        ids = {str(p.id) for p in get_plugin_host().list_installed()}
        return ids | {_plugin_id_of(i) for i in ids}
    except Exception:  # noqa: BLE001
        return set()


def _installed_other_ids() -> dict[str, set[str]]:
    """Installed personas, blocks, formations, prompts and webapps — each by its loader.

    These kinds carried no ``installed`` flag at all, so the Bay offered them forever.
    A kind whose lookup fails is left out (unknown, not "not installed").
    """
    out: dict[str, set[str]] = {}
    try:
        from navig.personas.resolver import discover_persona_paths  # noqa: PLC0415

        out["persona"] = {str(n) for n in discover_persona_paths()}
    except Exception:  # noqa: BLE001
        pass
    try:
        from navig.blocks.loader import blocks_by_id  # noqa: PLC0415

        out["block"] = set(blocks_by_id().keys())
    except Exception:  # noqa: BLE001
        pass
    try:
        from navig.platform.paths import config_dir as _config_dir  # noqa: PLC0415
        from navig.platform.paths import store_dir  # noqa: PLC0415

        for kind, root in (
            ("formation", store_dir() / "formations"),
            ("prompt", store_dir() / "prompts"),
            ("webapp", _config_dir() / "webapps"),
        ):
            out[kind] = {d.name for d in root.iterdir() if d.is_dir()} if root.is_dir() else set()
    except Exception:  # noqa: BLE001
        pass
    return out


def _installed_skill_ids() -> set[str]:
    """Ids of skills the agent can already pull in (user + builtin skill dirs)."""
    try:
        from navig.skills.loader import skills_by_id  # noqa: PLC0415

        return set(skills_by_id().keys())
    except Exception:  # noqa: BLE001
        return set()


def _plugin_id_of(slug: str) -> str:
    """Catalog plugin slugs are the package name (``navig-social``) or a bare id
    (``vault``); the plugin host keys installs by the bare id."""
    return slug[6:] if slug.startswith("navig-") else slug


def _license_tier() -> str:
    """The current effective Harbor tier name, ``free`` if the license is unreadable."""
    try:
        from navig.license import current_status  # noqa: PLC0415

        return str(current_status().effective_tier or "free")
    except Exception:  # noqa: BLE001
        return "free"


def _install_spec(install: str) -> str | None:
    """The bare spec ``/api/deck/catalog/install`` accepts, pulled from a bay
    item's ``install`` command — the ``github:navig-run/registry/…`` reference.

    Returns ``None`` for kinds acquired a different way (persona ``use``, block
    ``apply``, plugin ``install <name>``, an extension-bundled lens = manual): a surface renders those
    as the raw ``install`` command instead of a one-click Install button.
    """
    idx = install.find("github:")
    return install[idx:].split()[0] if idx >= 0 else None


def _enrich_bay_item(
    item: dict[str, Any],
    *,
    caps: list[str],
    tier_rank: int,
    installed_spaces: set[str],
    fail_open: bool,
    installed_plugins: set[str] | None = None,
    installed_skills: set[str] | None = None,
    installed_other: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Attach live entitlement (``unlocked``/``capability``) + ``installed`` to an item.

    The static web catalog can't carry live unlock state — this is the daemon's
    unique value-add for local surfaces (desktop OS / Anchor / Deck).
    ``installed`` is known for spaces, plugins and skills — the three kinds the
    daemon can enumerate locally; the other kinds carry no flag (unknown ≠ false).
    """
    pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
    model = str(pricing.get("model") or "free")
    included_in = pricing.get("includedInTier")
    slug = str(item.get("slug") or "")
    capability = f"item:{slug.lower()}" if model == "buy_once" and slug else None

    unlocked = True if fail_open else _is_unlocked(
        model=model,
        capability=capability,
        included_in=str(included_in) if included_in else None,
        caps=caps,
        tier_rank=tier_rank,
    )
    out = dict(item)
    out["unlocked"] = unlocked
    out["capability"] = capability
    out["spec"] = _install_spec(str(item.get("install") or ""))
    kind = item.get("kind")
    if kind == "space":
        from navig.spaces.contracts import normalize_space_name  # noqa: PLC0415

        out["installed"] = slug in installed_spaces or normalize_space_name(slug) in installed_spaces
    elif kind == "plugin" and installed_plugins is not None:
        out["installed"] = slug in installed_plugins or _plugin_id_of(slug) in installed_plugins
    elif kind == "skill" and installed_skills is not None:
        out["installed"] = slug in installed_skills
    elif installed_other is not None and kind in installed_other:
        out["installed"] = slug in installed_other[str(kind)]
    return out


def gather_bay_items(*, kind: str | None = None, surface: str | None = None) -> dict[str, Any]:
    """Load the served catalog, enrich each item with live entitlement, and filter.

    Returns the data payload (``items``/``count``/``generated_at``/``source``/
    ``license_tier``). Pure (no HTTP) so the daemon endpoint AND the
    ``navig_bay_list`` MCP tool share ONE implementation. Degrades to an empty
    list (``source: "unavailable"``) when the generated artifact is absent.
    """
    path = _bay_catalog_path()
    items: list[Any] = []
    generated_at = None
    source = "unavailable"
    if path is not None:
        data = _read_json(path)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            items = data["items"]
            generated_at = data.get("generatedAt") or data.get("generated_at")
            source = "bundled" if path.name == "bay-catalog.json" else "repo"

    fail_open = False
    try:
        caps, tier_rank = _live_caps_and_rank()
    except Exception:  # noqa: BLE001 — license down → show everything unlocked
        caps, tier_rank, fail_open = [], 0, True

    installed_spaces = _installed_space_ids()
    installed_plugins = _installed_plugin_ids()
    installed_skills = _installed_skill_ids()
    installed_other = _installed_other_ids()
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if kind and it.get("kind") != kind:
            continue
        if surface and surface not in (it.get("surfaces") or []):
            continue
        out.append(
            _enrich_bay_item(
                it,
                caps=caps,
                tier_rank=tier_rank,
                installed_spaces=installed_spaces,
                fail_open=fail_open,
                installed_plugins=installed_plugins,
                installed_skills=installed_skills,
                installed_other=installed_other,
            )
        )
    return {
        "items": out,
        "count": len(out),
        "generated_at": generated_at,
        "source": source,
        "license_tier": _license_tier(),
    }


async def handle_deck_bay(request: "web.Request") -> "web.Response":
    """GET /api/deck/bay — the FULL Harbor Bay catalog, enriched with live entitlement.

    Thin HTTP wrapper over :func:`gather_bay_items`. Optional filters ``?kind=`` /
    ``?surface=``. This is the ONE catalog every LOCAL surface (desktop OS,
    Anchor, Deck) reads; the ``navig_bay_list`` MCP tool serves the same data.
    """
    try:
        return _ok(
            gather_bay_items(
                kind=request.query.get("kind"),
                surface=request.query.get("surface"),
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("bay catalog failed")
        return _err(str(exc), 500)


def _bay_item(slug: str) -> dict[str, Any] | None:
    """Look up an item in the served catalog by slug (for server-side gating)."""
    path = _bay_catalog_path()
    if path is None:
        return None
    data = _read_json(path)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    return next(
        (it for it in items if isinstance(it, dict) and str(it.get("slug")) == slug),
        None,
    )


def _plugin_name(install: str) -> str | None:
    """The plugin name from a ``navig plugin install <name>`` command (last token)."""
    parts = install.split()
    return parts[-1] if len(parts) >= 2 and "install" in parts else None


_PIP_PACKAGE = re.compile(r"^navig-[a-z0-9][a-z0-9-]*$")


def _pip_package(install: str) -> str | None:
    """The first-party package a catalog install command names, or None.

    Catalog plugins install as ``pip install navig-audio`` or
    ``navig store install pip:navig-calendar``. Only a ``navig-*`` name is accepted, so the
    Bay can never be talked into installing an arbitrary package.
    """
    text = install.split("#", 1)[0]
    tokens = text.split()
    for i, tok in enumerate(tokens):
        if tok.startswith("pip:"):
            name = tok[4:]
        elif tok == "install" and i > 0 and tokens[i - 1] == "pip" and i + 1 < len(tokens):
            name = tokens[i + 1]
        else:
            continue
        return name if _PIP_PACKAGE.match(name) else None
    return None


def _rearm_entry_points() -> None:
    """Forget the entry-point once-guard so a freshly pip-installed plugin's
    module appears on the NEXT catalog GET — no daemon restart. Mirrors
    ``handle_deck_catalog_install`` and the store install action; keep the three
    in step. Best-effort — a discovery refresh must never fail an otherwise
    successful install."""
    try:
        from navig.modules.registry import reset_entry_points  # noqa: PLC0415

        reset_entry_points()
    except Exception:  # noqa: BLE001
        pass


def acquire_bay_item(slug: str, kind: str = "", install: str = "") -> dict[str, Any]:
    """Acquire a Bay item the RIGHT way for its kind — SYNCHRONOUS, so it's safe
    to call from an executor (the HTTP endpoint) OR directly (the
    ``navig_bay_acquire`` MCP tool). ``kind``/``install`` are looked up from the
    served catalog when omitted (the MCP tool passes only a slug; the HTTP
    endpoint passes them from the request). Dispatches rather than forcing a
    uniform "install":

    - github-installable (space/skill/formation/prompt/webapp) **and** plugin →
      ``{"action": "install", "installed": True}``.
    - **persona** → ``{"action": "activate"}`` — a switch in the caller's context.
    - **block** → ``{"action": "apply"}`` — blocks EXECUTE with inputs/approvals;
      the caller opens the apply flow (never silently run from a button).
    - **lens** with a ``github:`` spec (a store Lens, ``.user.js``) installs like the
      github kinds above, through the Lens store; a lens bundled in the Chrome
      extension (no spec — "Enable in the Dock bar") stays manual.
    - **unknown** → ``{"action": "manual", "command": install}``.
    - priced+locked → ``{"action": "locked", "capability", "tier_required"}``.
    - bad input / install failure → ``{"action": "error", "error": ...}``.

    The entitlement gate is server-side — a client's ``unlocked`` flag is never
    trusted.
    """
    if not slug:
        return {"action": "error", "error": "'slug' is required"}

    # One catalog lookup: derive kind/install if the caller omitted them, and
    # re-derive unlock (the server-side entitlement gate).
    item = _bay_item(slug)
    if item is not None:
        kind = kind or str(item.get("kind") or "")
        install = install or str(item.get("install") or "")
        pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
        model = str(pricing.get("model") or "free")
        included_in = pricing.get("includedInTier")
        capability = f"item:{slug.lower()}" if model == "buy_once" else None
        try:
            caps, tier_rank = _live_caps_and_rank()
            unlocked = _is_unlocked(
                model=model,
                capability=capability,
                included_in=str(included_in) if included_in else None,
                caps=caps,
                tier_rank=tier_rank,
            )
        except Exception:  # noqa: BLE001 — license down → fail open
            unlocked = True
        if not unlocked:
            return {
                "action": "locked",
                "capability": capability or f"item:{slug.lower()}",
                "tier_required": str(included_in) if included_in else None,
            }

    spec = _install_spec(install)
    if spec:
        # Gate the SPEC too (mirrors handle_deck_catalog_install): the slug-based gate above is
        # skipped when _bay_item(slug) misses, so a priced registry item installed via a
        # mismatched slug would otherwise bypass the paywall the install door enforces.
        lock = _spec_lock(spec)
        if lock is not None:
            return {
                "action": "locked",
                "capability": lock["capability"],
                "tier_required": lock["tier_required"],
            }
        from navig.commands.install import install_asset  # noqa: PLC0415

        try:
            install_asset(spec, force=False, upgrade=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bay acquire (install) failed for %s: %s", slug, exc)
            return {"action": "error", "error": f"install failed: {exc}"}
        _rearm_entry_points()  # the fresh install must appear on the next catalog GET
        return {"slug": slug, "action": "install", "installed": True}

    if kind == "plugin" and (package := _pip_package(install)):
        # The plugin host installs dirs, zips and git repos — it never runs pip, so handing it
        # "navig-audio" could only fail. Install the package into the daemon's own runtime.
        from navig.cli.providers import pip_install  # noqa: PLC0415

        try:
            pip_install(package)
        except ValueError as exc:
            logger.warning("bay acquire (pip) failed for %s: %s", package, exc)
            return {"action": "error", "error": str(exc)}
        import importlib  # noqa: PLC0415

        importlib.invalidate_caches()  # the new distribution must be visible to metadata
        _rearm_entry_points()
        return {"slug": slug, "action": "install", "installed": True}

    if kind == "plugin":
        name = _plugin_name(install)
        if not name:
            return {"action": "error", "error": "could not resolve plugin name from install command"}
        from navig.plugins.host import PluginHost  # noqa: PLC0415

        try:
            PluginHost().install(name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bay acquire (plugin) failed for %s: %s", name, exc)
            return {"action": "error", "error": f"plugin install failed: {exc}"}
        _rearm_entry_points()  # the fresh plugin must appear on the next catalog GET
        return {"slug": slug, "action": "install", "installed": True}

    if kind == "persona":
        return {"slug": slug, "action": "activate"}
    if kind == "block":
        return {"slug": slug, "action": "apply"}
    return {"slug": slug, "action": "manual", "command": install}


async def handle_deck_bay_acquire(request: "web.Request") -> "web.Response":
    """POST { slug, kind, install } — acquire a Bay item (see :func:`acquire_bay_item`).

    Thin HTTP wrapper: runs the (blocking) acquire off the event loop, then maps
    ``action: "locked"`` → 402 (the unlock payload) and ``action: "error"`` → 400.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err("invalid JSON")
    slug = str(body.get("slug") or "").strip()
    kind = str(body.get("kind") or "").strip()
    install = str(body.get("install") or "").strip()

    import asyncio  # noqa: PLC0415

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: acquire_bay_item(slug, kind, install))

    action = result.get("action")
    if action == "locked":
        from navig.license.gate import capability_payload  # noqa: PLC0415

        payload = capability_payload(result["capability"], _license_tier())
        if result.get("tier_required"):
            payload["tier_required"] = result["tier_required"]
        return web.json_response(payload, status=402)
    if action == "error":
        return _err(result.get("error") or "acquire failed", 400)
    if action == "install":
        # Mirror handle_deck_catalog_install: tell live surfaces (desktop OS,
        # deck) to re-hydrate so the just-acquired app/plugin appears without a
        # manual refresh. Best-effort — a broken SSE emit must not fail the install.
        try:
            from navig.gateway.deck.routes.modules import emit_modules_update  # noqa: PLC0415

            await emit_modules_update(request, {"kind": "acquire", "slug": result.get("slug") or slug})
        except Exception:  # noqa: BLE001
            logger.debug("bay acquire: modules_update emit skipped", exc_info=True)
    return _ok(result)


def _find_market_pricing(spec: str) -> tuple[str, dict[str, Any]] | None:
    """Resolve a catalog install spec to (item_id, marketplace block).

    Only specs pointing into OUR community registry are resolvable; anything
    else (arbitrary github/local specs) returns None and installs ungated.
    """
    # One spelling before the gate: a spec naming the registry's former name is OUR
    # registry, so it must be priced — never waved through as "someone else's".
    spec = _normalize_registry_spec(spec)
    # Parse exactly as the installer does. A literal prefix test was bypassable: GitHub
    # owner/repo names are case-insensitive (`github:NAVIG-RUN/…` installs the same
    # folder) and the installer also accepts `space:`/`skill:`/bare `owner/repo/…` and an
    # `@ref` suffix — each of those installed a priced item ungated.
    from navig.commands.install import _parse_spec  # noqa: PLC0415

    try:
        info = _parse_spec(spec)
    except ValueError:
        return None
    if str(info["owner"]).lower() != _MARKET_OWNER or str(info["repo"]).lower() != _MARKET_REPO:
        return None
    parts = [p for p in info["subpath"] if p]
    if len(parts) < 2:
        return None
    cid = parts[-1]
    root = _community_root()
    if root is None:
        return None

    def _block_from(entries: Any) -> tuple[str, dict[str, Any]] | None:
        if not isinstance(entries, list):
            return None
        for e in entries:
            if not isinstance(e, dict):
                continue
            # Match the FOLDER too: `social-media` installs from `spaces/social-media-space`
            # (its registry `path`), and an id-only match let that paid space install
            # ungated. The entry's own id is what the grant key is built from, so it —
            # not the path — is what the caller gets back.
            if cid in (str(e.get("id") or e.get("name")), str(e.get("path") or "")):
                m = e.get("marketplace")
                return str(e.get("id") or e.get("name")), (m if isinstance(m, dict) else {})
        return None

    # Spaces + skills registries, personas persona.json — tolerant walk.
    for reg_path, key in (
        (root / "spaces" / "registry.json", "spaces"),
        (root / "cli-skills" / "registry.json", "skills"),
        (root / "ai-skills" / "registry.json", "skills"),
        (root / "personas" / "registry.json", "personas"),
    ):
        reg = _read_json(reg_path)
        if isinstance(reg, dict):
            hit = _block_from(reg.get(key) or reg.get("items"))
            if hit is not None:
                return hit
    meta = _read_json(root / "personas" / cid / "persona.json")
    if isinstance(meta, dict):
        m = meta.get("marketplace")
        return cid, (m if isinstance(m, dict) else {})
    return None


def _spec_lock(spec: str) -> dict[str, Any] | None:
    """The ONE spec-side entitlement gate, shared by both install doors.

    If *spec* resolves to a LOCKED priced item in OUR community registry for the current
    license, return its unlock info ``{capability, tier_required, price_cents}``; else None
    (unlocked, or a foreign/arbitrary spec that installs ungated by design). Both the catalog
    install door AND bay-acquire route the spec through this so a priced item can't slip through
    one door while the other blocks it — acquire used to gate only the slug, so a mismatched slug
    bypassed the paywall the install door enforces.
    """
    resolved = _find_market_pricing(spec)
    if resolved is None:
        return None
    cid, market = resolved
    pricing = _pricing_fields(cid, market)
    if pricing["unlocked"]:
        return None
    return {
        "capability": pricing["capability"] or f"item:{cid.lower()}",
        "tier_required": pricing["included_in_tier"],
        "price_cents": pricing["price_cents"],
    }


async def handle_deck_catalog_install(request: "web.Request") -> "web.Response":
    """POST { spec, force?, upgrade? } — install a Harbor Bay item via the additive installer.

    Priced items are entitlement-gated: a locked `tier`/`buy_once` item returns
    the structured 402 (same shape as `requires_capability`) so surfaces render
    the one unlock CTA. v1 honesty note: content remains publicly fetchable;
    authenticated delivery lands in v1.5 — this gate is UX truth, not DRM.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err("invalid JSON")
    spec = str(body.get("spec") or "").strip()
    if not spec:
        return _err("'spec' is required")
    force = bool(body.get("force"))
    upgrade = bool(body.get("upgrade"))

    lock = _spec_lock(spec)  # the shared spec-side entitlement gate (see also acquire_bay_item)
    if lock is not None:
        from navig.license.gate import capability_payload  # noqa: PLC0415

        try:
            from navig.license import current_status  # noqa: PLC0415

            tier = current_status().effective_tier or "free"
        except Exception:  # noqa: BLE001
            tier = "free"
        payload = capability_payload(lock["capability"], str(tier))
        if lock["tier_required"]:
            payload["tier_required"] = lock["tier_required"]
        if lock["price_cents"]:
            payload["price_cents"] = lock["price_cents"]
        return web.json_response(payload, status=402)

    import asyncio  # noqa: PLC0415

    from navig.commands.install import install_asset  # noqa: PLC0415

    loop = asyncio.get_event_loop()
    try:
        # install_asset does blocking network/file I/O — keep the event loop free.
        await loop.run_in_executor(None, lambda: install_asset(spec, force=force, upgrade=upgrade))
    except Exception as exc:  # noqa: BLE001
        logger.warning("catalog install failed for %s: %s", spec, exc)
        return _err(f"install failed: {exc}", 400)
    # No-restart contract (rearm discovery + push a refresh) — the ONE shared helper.
    from navig.gateway.deck.routes.modules import notify_modules_changed  # noqa: PLC0415

    await notify_modules_changed(request, {"kind": "install", "spec": spec})
    return _ok({"spec": spec, "installed": True})


async def _set_space_enabled(sid: str, enabled: bool) -> bool:
    """Toggle a space's registry flag off the loop (set_enabled reads + rewrites the registry file)."""
    from navig.spaces import registry as _registry  # noqa: PLC0415

    return await asyncio.to_thread(lambda: _registry.set_enabled(sid, enabled))


async def handle_deck_space_enable(request: "web.Request") -> "web.Response":
    sid = request.match_info.get("id", "")
    ok = await _set_space_enabled(sid, True)
    return _ok({"id": sid, "enabled": True}) if ok else _err("not registered", 404)


async def handle_deck_space_disable(request: "web.Request") -> "web.Response":
    sid = request.match_info.get("id", "")
    ok = await _set_space_enabled(sid, False)
    return _ok({"id": sid, "enabled": False}) if ok else _err("not registered", 404)


async def handle_deck_space_apps(request: "web.Request") -> "web.Response":
    """POST { apps: [module ids] } — set the space's pinned-apps allow-list.

    Writes the manifest's `apps` array (order preserved; [] = show all). This is
    the desktop sidebar's per-space view-filter — it never touches the global
    module enablement. Unknown manifest keys are preserved; a bare `.navig/`
    space gets a minimal `space.json` created. YAML manifests are read-only
    here (rare, community shape) → 409 with a clear message.
    """
    try:
        from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415
        from navig.spaces.space_manifest import (  # noqa: PLC0415
            ManifestNotWritable,
            set_manifest_field,
        )

        sid = request.match_info.get("id", "")
        cfg = discover_space_paths(include_disabled=True).get(sid)
        if cfg is None:
            return _err(f"space '{sid}' not found", 404)

        body = await request.json()
        apps = body.get("apps")
        if not isinstance(apps, list) or not all(isinstance(a, str) and a.strip() for a in apps):
            return _err("body must be { apps: [module ids] }", 400)
        apps = [a.strip() for a in apps]

        try:
            set_manifest_field(Path(cfg.path), "apps", apps, id_hint=sid)
        except ManifestNotWritable as exc:
            return _err(str(exc), 409)
        return _ok({"id": sid, "apps": apps})
    except Exception as exc:  # noqa: BLE001
        logger.exception("spaces/apps failed")
        return _err(str(exc), 500)


async def handle_deck_space_app_sections(request: "web.Request") -> "web.Response":
    """POST { sections: { appId: { order?: [ids], hidden?: [ids] } } }.

    Sets the space's per-app SECTION layouts — the desktop sidebar's second-level
    view-filter (which sections an app shows, in which order). Same contract as
    ``/apps`` one level down: unknown manifest keys are preserved, a bare
    ``.navig/`` gets a minimal ``space.json``, YAML manifests are read-only (409).

    Validation is strict on SHAPE and silent on CONTENT: ids are opaque strings
    owned by the desktop's section registry, and the daemon has no business
    deciding which of them are real — an id that stops matching simply stops
    applying (see ``SpaceManifest.app_sections``).
    """
    try:
        from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415
        from navig.spaces.space_manifest import (  # noqa: PLC0415
            ManifestNotWritable,
            set_manifest_field,
        )

        sid = request.match_info.get("id", "")
        cfg = discover_space_paths(include_disabled=True).get(sid)
        if cfg is None:
            return _err(f"space '{sid}' not found", 404)

        body = await request.json()
        sections = body.get("sections")
        if not isinstance(sections, dict):
            return _err("body must be { sections: { appId: { order?, hidden? } } }", 400)

        clean: dict[str, dict[str, list[str]]] = {}
        for app_id, layout in sections.items():
            if not isinstance(app_id, str) or not app_id.strip():
                return _err("every key must be a non-empty app id", 400)
            if not isinstance(layout, dict):
                return _err(f"'{app_id}' must map to {{ order?, hidden? }}", 400)
            entry: dict[str, list[str]] = {}
            for key in ("order", "hidden"):
                ids = layout.get(key)
                if ids is None:
                    continue
                if not isinstance(ids, list) or not all(isinstance(s, str) and s.strip() for s in ids):
                    return _err(f"'{app_id}.{key}' must be a list of section ids", 400)
                if ids:
                    entry[key] = [s.strip() for s in ids]
            # Drop empty entries so clearing a layout removes the key entirely
            # rather than leaving `{"finance": {}}` behind forever.
            if entry:
                clean[app_id.strip()] = entry

        try:
            set_manifest_field(Path(cfg.path), "app_sections", clean, id_hint=sid)
        except ManifestNotWritable as exc:
            return _err(str(exc), 409)
        return _ok({"id": sid, "app_sections": clean})
    except Exception as exc:  # noqa: BLE001
        logger.exception("spaces/app-sections failed")
        return _err(str(exc), 500)


async def handle_deck_space_books(request: "web.Request") -> "web.Response":
    """POST { books: "Company" | null } — set (or clear) the space's finance BOOK.

    Writes the manifest's `books` key — the separate ledger a space keeps (the
    finance app reads it for the ACTIVE space; empty/null ⇒ the default personal
    ledger). Sibling of `/spaces/{id}/apps`: unknown space → 404, a YAML /
    unreadable manifest → 409, a bad body → 400.
    """
    try:
        from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415
        from navig.spaces.space_manifest import (  # noqa: PLC0415
            ManifestNotWritable,
            set_manifest_field,
        )

        sid = request.match_info.get("id", "")
        cfg = discover_space_paths(include_disabled=True).get(sid)
        if cfg is None:
            return _err(f"space '{sid}' not found", 404)

        body = await request.json()
        if not isinstance(body, dict) or "books" not in body:
            return _err("body must be { books: <name> | null }", 400)
        raw = body.get("books")
        if raw is not None and not isinstance(raw, str):
            return _err("body must be { books: <name> | null }", 400)
        value = (raw.strip() or None) if isinstance(raw, str) else None  # empty/null ⇒ clear

        try:
            set_manifest_field(Path(cfg.path), "books", value, id_hint=sid)
        except ManifestNotWritable as exc:
            return _err(str(exc), 409)
        return _ok({"id": sid, "books": value})
    except Exception as exc:  # noqa: BLE001
        logger.exception("spaces/books failed")
        return _err(str(exc), 500)


# What a person may type into the desktop's "Edit details" dialog. The cap is generous
# for a name and a sentence or two; it exists so a pasted document can't become a
# manifest field every surface then has to render.
_SPACE_INFO_LIMITS = {"display_name": 80, "description": 500, "category": 40}
_MANIFEST_ID_KEYS = ("id", "space_id", "spaceId", "name")


async def handle_deck_space_info(request: "web.Request") -> "web.Response":
    """POST { display_name?, description?, category? } (str | null) — a space's identity text.

    The desktop's workspace switcher edits a space's name and description here, so
    the brain (Telegram, `navig space list`, the deck) shows what the operator typed
    instead of the folder slug. Same safe writer and contract as ``/books``: unknown
    space → 404, YAML / unreadable manifest → 409, bad body → 400; an empty string or
    null clears the field.

    ``display_name`` is the LAST fallback of ``SpaceManifest.resolved_id``. A manifest
    with no id key of its own would therefore start answering to "Company Business"
    the moment a display name landed in it — so the route pins ``id`` first.
    """
    try:
        from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415
        from navig.spaces.space_manifest import (  # noqa: PLC0415
            ManifestNotWritable,
            load_space_manifest,
            set_manifest_field,
        )

        sid = request.match_info.get("id", "")
        cfg = discover_space_paths(include_disabled=True).get(sid)
        if cfg is None:
            return _err(f"space '{sid}' not found", 404)

        body = await request.json()
        if not isinstance(body, dict) or not any(k in body for k in _SPACE_INFO_LIMITS):
            return _err("body must be { display_name?, description?, category? } (string | null each)", 400)
        updates: dict[str, str | None] = {}
        for key, limit in _SPACE_INFO_LIMITS.items():
            if key not in body:
                continue
            raw = body[key]
            if raw is not None and not isinstance(raw, str):
                return _err(f"'{key}' must be a string or null", 400)
            value = (raw.strip() or None) if isinstance(raw, str) else None
            if value is not None and len(value) > limit:
                return _err(f"'{key}' is longer than {limit} characters", 400)
            updates[key] = value

        space_dir = Path(cfg.path)

        def _write() -> dict[str, Any]:
            manifest = load_space_manifest(space_dir)
            if manifest.source_path is not None and not any(manifest.get(k) for k in _MANIFEST_ID_KEYS):
                set_manifest_field(space_dir, "id", sid, id_hint=sid)
            for key, value in updates.items():
                set_manifest_field(space_dir, key, value, id_hint=sid)
            after = load_space_manifest(space_dir)
            # A manifest nesting its fields under "space:" lets the nested copy win on
            # read, so a top-level write would report success and change nothing.
            for key, value in updates.items():
                if (after.get(key) or None) != value:
                    raise ManifestNotWritable(f"space manifest keeps `{key}` nested — edit it by hand")
            return {
                "id": sid,
                "name": after.resolved_name or sid,
                "display_name": after.get("display_name"),
                "description": after.get("description") or "",
                "category": after.get("category"),
            }

        try:
            return _ok(await asyncio.to_thread(_write))
        except ManifestNotWritable as exc:
            return _err(str(exc), 409)
    except Exception as exc:  # noqa: BLE001
        logger.exception("spaces/info failed")
        return _err(str(exc), 500)


async def handle_deck_space_activate(request: "web.Request") -> "web.Response":
    """Set the active space (binds the working dir, like `navig space switch`)."""
    sid = request.match_info.get("id", "")

    def _activate() -> dict[str, Any] | None:
        # Discovery + manifest load + .resolve() + three writes (working-dir, registry, config
        # mirror) are all blocking — run them off the event loop. Returns None for an unknown
        # space so the caller can 404. `_set_active_space` stays best-effort inside the thread.
        from navig.spaces import registry as _registry  # noqa: PLC0415
        from navig.spaces.active import set_active_working_dir  # noqa: PLC0415
        from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415
        from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

        cfg = discover_space_paths(include_disabled=True).get(sid)
        if cfg is None:
            return None
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
        return {"id": sid, "active": True, "working_dir": str(working_dir)}

    try:
        result = await asyncio.to_thread(_activate)
        if result is None:
            return _err(f"space '{sid}' not found", 404)
        return _ok(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("spaces/activate failed")
        return _err(str(exc), 500)


async def handle_deck_space_register(request: "web.Request") -> "web.Response":
    """POST { path, auto? } — register an external .navig/ folder (enabled).

    ``auto: true`` marks a *background* back-fill (NAVIG OS syncing its workspace
    list into the registry). That caller must never resurrect a space the operator
    forgot — and never scaffold a ``.navig/`` marker onto one — so a forgotten path
    answers ``{skipped: "forgotten"}`` with its tombstone intact and a bare folder
    answers ``{skipped: "not_a_space"}`` (the caller may scaffold, then retry
    explicitly). Without ``auto`` the contract is unchanged: an explicit register
    is the documented way to bring a forgotten space back.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err("invalid JSON")
    raw = (body.get("path") or "").strip()
    if not raw:
        return _err("'path' is required")
    auto = bool(body.get("auto"))
    from navig.spaces import registry as _registry  # noqa: PLC0415
    from navig.spaces.contracts import normalize_space_name  # noqa: PLC0415
    from navig.spaces.space_manifest import is_space_dir, load_space_manifest  # noqa: PLC0415

    target = Path(raw).expanduser()
    if auto:
        if _registry.is_forgotten(target):
            return _ok({"skipped": "forgotten"})
        if not target.is_dir() or not is_space_dir(target):
            return _ok({"skipped": "not_a_space"})
    elif not target.is_dir() or not is_space_dir(target):
        return _err("not a space (needs a .navig/ directory)", 400)
    manifest = load_space_manifest(target)
    sid = normalize_space_name(manifest.resolved_id or target.name)
    entry = _registry.register(target, id=sid, name=manifest.resolved_name or target.name, source="external", enabled=True)
    return web.json_response({"ok": True, "data": entry}, status=201)


def _relations_space_or_err(request: "web.Request") -> "tuple[Any, None] | tuple[None, web.Response]":
    """Resolve ``/spaces/{id}/…`` to (space_cfg, None) or (None, error_response)."""
    from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415

    sid = request.match_info.get("id", "")
    cfg = discover_space_paths(include_disabled=True).get(sid)
    if cfg is None:
        return None, _err("not registered", 404)
    return cfg, None


async def handle_deck_space_relations(request: "web.Request") -> "web.Response":
    """GET /spaces/{id}/relations — the shared graph reader's payload (parent, links,
    children, inbound links, availability, ``parent_cycle``). Read-only."""
    from navig.spaces import relations as _relations  # noqa: PLC0415
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    cfg, err = _relations_space_or_err(request)
    if err is not None:
        return err
    own = load_space_manifest(cfg.path).resolved_id or request.match_info.get("id", "")
    _, payload = _relations.related_graph(cfg.path, space_id=own)
    return _ok(payload)


async def handle_deck_space_relations_update(request: "web.Request") -> "web.Response":
    """POST /spaces/{id}/relations — incremental, validated mutations.

    Body: ``{op: "link", space, include?, exclude?, profile?}`` ·
    ``{op: "unlink", space}`` · ``{op: "parent", space | null}`` (null clears).
    Same write-time rules as the CLI: unregistered targets, self-links, bad access
    and parent cycles answer 400 with the reason; success returns the updated
    graph payload so a client never refetches to converge.
    """
    from navig.spaces import relations as _relations  # noqa: PLC0415
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    cfg, err = _relations_space_or_err(request)
    if err is not None:
        return err
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err("invalid JSON")
    op = str(body.get("op") or "").strip()
    try:
        if op == "link":
            target = str(body.get("space") or "").strip()
            if not target:
                return _err("'space' is required for op=link")
            _relations.link(
                cfg.path,
                target,
                include=tuple(body.get("include") or ()),
                exclude=tuple(body.get("exclude") or ()),
                profile=body.get("profile"),
            )
        elif op == "unlink":
            target = str(body.get("space") or "").strip()
            if not target:
                return _err("'space' is required for op=unlink")
            _relations.unlink(cfg.path, target)
        elif op == "parent":
            space = body.get("space")
            _relations.set_parent(cfg.path, str(space).strip() if space else None)
        else:
            return _err("op must be one of: link, unlink, parent")
    except ValueError as exc:
        return _err(str(exc), 400)
    own = load_space_manifest(cfg.path).resolved_id or request.match_info.get("id", "")
    _, payload = _relations.related_graph(cfg.path, space_id=own)
    return _ok(payload)


async def handle_deck_space_linked_context(request: "web.Request") -> "web.Response":
    """GET /spaces/{id}/context?query= — what an agent may read from the links.

    Mirrors the ``/context/snapshot`` shape (``_ok({"snapshot": …})``) but scoped to
    the CROSS-Space half: provenance per item, deny-list, containment, budgets and
    dedupe already applied by ``navig.spaces.linked_context`` (read-only). ``query``
    is the caller's focus note, echoed back so runs stay comparable.
    """
    from navig.spaces import linked_context as _lc  # noqa: PLC0415
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    cfg, err = _relations_space_or_err(request)
    if err is not None:
        return err
    own = load_space_manifest(cfg.path).resolved_id or request.match_info.get("id", "")
    focus = request.rel_url.query.get("query") or None

    def _gather() -> Any:
        ctx = _lc.gather_linked(cfg.path, space_id=own)
        return {
            "snapshot": {
                "space": ctx.space,
                "parent_cycle": ctx.parent_cycle,
                "unavailable": [{"id": i, "reason": r} for i, r in ctx.unavailable],
                "chars_total": ctx.chars_total,
                "items": [
                    {**item.provenance, "source": item.source, "size": item.size} for item in ctx.items
                ],
            },
            "focus": focus,
        }

    return _ok(await asyncio.to_thread(_gather))


async def handle_deck_space_repath(request: "web.Request") -> "web.Response":
    """POST { old_path, new_path } — the space's folder moved; follow it.

    The desktop's "Change folder…" calls this after the operator points a workspace at
    the folder's new place. A registered space keeps its row — ``enabled``, ``trusted``
    and the active pointer included (``registry.repath``); a space the registry only
    knew by discovery (a folder under the spaces root) is registered at the new place.
    The new folder must be a space (have ``.navig/``); nothing on disk is moved here.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err("invalid JSON")
    old_raw = str(body.get("old_path") or "").strip() if isinstance(body, dict) else ""
    new_raw = str(body.get("new_path") or "").strip() if isinstance(body, dict) else ""
    if not old_raw or not new_raw:
        return _err("'old_path' and 'new_path' are required")
    from navig.spaces import registry as _registry  # noqa: PLC0415
    from navig.spaces.contracts import normalize_space_name  # noqa: PLC0415
    from navig.spaces.space_manifest import is_space_dir, load_space_manifest  # noqa: PLC0415

    target = Path(new_raw).expanduser()
    if not target.is_dir() or not is_space_dir(target):
        return _err("the new folder is not a space (needs a .navig/ directory)", 400)

    def _move() -> dict[str, Any]:
        if _registry.repath(old_raw, target):
            return {"path": str(target), "carried": True}
        manifest = load_space_manifest(target)
        sid = normalize_space_name(manifest.resolved_id or target.name)
        _registry.ensure_registered(target, id=sid, name=manifest.resolved_name or target.name, source="external")
        return {"path": str(target), "carried": False}

    return _ok(await asyncio.to_thread(_move))


async def handle_deck_space_forget(request: "web.Request") -> "web.Response":
    """POST { path } — drop a space's registry row (the folder is not touched).

    Called when the desktop removes a workspace, so a deleted space does not linger as
    a registry row pointing at nothing. Answers ``{forgotten: false}`` (not an error)
    for a path the registry never held — discovery-only spaces have no row to drop.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err("invalid JSON")
    raw = str(body.get("path") or "").strip() if isinstance(body, dict) else ""
    if not raw:
        return _err("'path' is required")
    from navig.spaces import registry as _registry  # noqa: PLC0415

    forgotten = await asyncio.to_thread(_registry.forget, str(Path(raw).expanduser()))
    return _ok({"path": raw, "forgotten": bool(forgotten)})
