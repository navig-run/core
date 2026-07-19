"""Deck catalog + spaces-registry routes.

Powers the navig-deck "Spaces" control panel + the catalog/marketplace:

    GET  /api/deck/spaces/scan            → discovered spaces (manifest + progress + enabled + active)
    GET  /api/deck/catalog                → installed + available (spaces · personas · packages)
    POST /api/deck/spaces/{id}/enable      → show in deck/switcher
    POST /api/deck/spaces/{id}/disable     → hide (folder still works when you're in it)
    POST /api/deck/spaces/{id}/activate    → set the active space (binds the working dir)
    POST /api/deck/spaces/{id}/apps        → set the pinned-apps view-filter (allow-list)
    POST /api/deck/spaces/{id}/books       → set the finance BOOK (separate ledger; null = default)
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
        # Pinned apps (desktop sidebar view-filter): non-empty = only these, in
        # this order; empty = all. Never affects enablement (that's global).
        "app_allowlist": manifest.app_allowlist,
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
    from navig.platform.paths import config_dir

    candidates: list[Path] = [config_dir() / "community"]
    # Walk up from this file, checking in precedence order per parent: a sibling
    # `navig-community` checkout (polyrepo), then a `registry/community` mirror
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


# The community marketplace lives in the navig-run/community GitHub repo.
_MARKET_OWNER = "navig-run"
_MARKET_REPO = "community"


def _titleize(s: str) -> str:
    return s.replace("-space", "").replace("-", " ").replace("_", " ").strip().title()


# Entitlement primitives now live in navig.license.entitlement (the one canonical home,
# neutral to both this deck route and the hub Store collector). Re-exported under the
# historical private names so callers here — and test_entitlement_parity.py, which imports
# `catalog._TIER_RANK` — keep working unchanged.
from navig.license.entitlement import TIER_RANK as _TIER_RANK  # noqa: E402
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
    """Ids of spaces already installed locally (for the catalog ``installed`` flag)."""
    try:
        from navig.spaces import registry as _registry  # noqa: PLC0415

        return {str(e.get("id")) for e in _registry.load_registry().get("spaces", [])}
    except Exception:  # noqa: BLE001
        return set()


def _license_tier() -> str:
    """The current effective Harbor tier name, ``free`` if the license is unreadable."""
    try:
        from navig.license import current_status  # noqa: PLC0415

        return str(current_status().effective_tier or "free")
    except Exception:  # noqa: BLE001
        return "free"


def _install_spec(install: str) -> str | None:
    """The bare spec ``/api/deck/catalog/install`` accepts, pulled from a bay
    item's ``install`` command — the ``github:navig-run/community/…`` reference.

    Returns ``None`` for kinds acquired a different way (persona ``use``, block
    ``apply``, plugin ``install <name>``, lens = manual): a surface renders those
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
) -> dict[str, Any]:
    """Attach live entitlement (``unlocked``/``capability``) + ``installed`` to an item.

    The static web catalog can't carry live unlock state — this is the daemon's
    unique value-add for local surfaces (desktop OS / Anchor / Deck).
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
    if item.get("kind") == "space":
        out["installed"] = slug in installed_spaces
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
    - **lens / unknown** → ``{"action": "manual", "command": install}``.
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
    prefix = f"github:{_MARKET_OWNER}/{_MARKET_REPO}/"
    if not spec.startswith(prefix):
        return None
    rest = spec[len(prefix):].strip("/")
    parts = rest.split("/")
    if len(parts) < 2:
        return None
    cid = parts[-1]
    root = _community_root()
    if root is None:
        return None

    def _block_from(entries: Any) -> dict[str, Any] | None:
        if not isinstance(entries, list):
            return None
        for e in entries:
            if isinstance(e, dict) and str(e.get("id") or e.get("name")) == cid:
                m = e.get("marketplace")
                return m if isinstance(m, dict) else {}
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
            block = _block_from(reg.get(key) or reg.get("items"))
            if block is not None:
                return cid, block
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


async def handle_deck_space_enable(request: "web.Request") -> "web.Response":
    from navig.spaces import registry as _registry  # noqa: PLC0415

    sid = request.match_info.get("id", "")
    return _ok({"id": sid, "enabled": True}) if _registry.set_enabled(sid, True) else _err("not registered", 404)


async def handle_deck_space_disable(request: "web.Request") -> "web.Response":
    from navig.spaces import registry as _registry  # noqa: PLC0415

    sid = request.match_info.get("id", "")
    return _ok({"id": sid, "enabled": False}) if _registry.set_enabled(sid, False) else _err("not registered", 404)


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
