"""
NAVIG module registry — the canonical, data-driven catalog of operator modules.

A **module** is a capability unit that a *surface* (navig-os, deck, CLI, Telegram)
renders: the toggleable, tier-gated operator "Apps" (Finance · DevOps · Goals ·
Life · Projects), launchers (navig-menu), primitives (navig-vault), and installed
plugins surfaced as capability modules. Surfaces read THIS registry instead of
hardcoding tiles (kills the duplicated `MODULES` array in os `lib/modules.ts` and
the deck `apps-section.tsx`).

Gating is **capability-based** and matches the Harbor entitlement model
(`license/tiers.json` + `@requires_capability`): a module names a `capability`
(one of the canonical capability flags, or `None` for always-free); it is *locked*
when that capability isn't in the current license's capability set. Real
entitlement is server-enforced here — surfaces only reflect it.

The enabled/disabled state is a small user override persisted in config
(`modules.overrides` = `{id: bool}`); a module with no override falls back to its
`default_enabled`. This is the server-canonical source the deck + os consume via
`GET /api/deck/modules`.

The former "Proactive Assistant Modules" (auto_detection/…) that shared this dir
now live in `navig.proactive` — this package is the module platform only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

_OVERRIDES_KEY = "modules.overrides"


class ModuleKind(str, Enum):
    APP = "app"              # operator app tile (finance/life/…) backed by a core deck route
    LAUNCHER = "launcher"    # standalone tool launched by NAVIG (navig-menu)
    PRIMITIVE = "primitive"  # core depends on it (navig-vault)
    SERVICE = "service"      # long-running built-in (gateway/telegram)
    UI = "ui"               # pure surface/view
    PLUGIN = "plugin"        # an installed CC/NAVIG plugin surfaced as a module
    WEBAPP = "webapp"        # a URL as a first-class themed desktop window (Harbor Bay item)


@dataclass
class ModuleDef:
    """One catalog entry. `capability=None` → always available (Free)."""

    id: str
    label: str
    description: str
    kind: ModuleKind = ModuleKind.APP
    category: str = "operate"          # operate | grow | build | tools | system
    icon: str = "box"                  # lucide icon name (surface-agnostic)
    capability: str | None = None      # Harbor capability flag that unlocks it
    min_tier: str | None = None        # human label for the lock (e.g. "plus"); display-only
    surfaces: list[str] = field(default_factory=list)   # e.g. ["os-tile:finance", "cli:finance"]
    requires: list[str] = field(default_factory=list)   # e.g. ["gateway", "vault"]
    default_enabled: bool = True
    dev_only: bool = False
    source: str = "builtin"            # builtin | plugin | launcher | entrypoint
    # ── App-surface metadata (deck→desktop apps migration, locked 2026-07-09) ─
    scope: str = "brain"               # brain = one-of-me data | space = per-space data
    app_category: str | None = None    # Money·Comms·Create·Life·Systems·Knowledge·Security
    hidden: bool = False               # hidden tile (kept for deep-links / palette)
    merged_into: str | None = None     # host app id when this app renders as a tab
    # Rich detail copy for the app's info page (surfaces render it; a surface
    # falls back to its own curated copy when these are None). Plugins set these
    # to give their app a full detail page without editing surface code.
    about: list[str] | None = None      # "About" paragraphs
    features: list[str] | None = None   # "What's inside" bullets
    # Plugin-declared settings fields, rendered on the app's `apps/<id>/settings`
    # page by the desktop OS (values persist via the generic
    # GET/POST /api/deck/apps/{id}/settings endpoint → apps.<id>.settings.*).
    # Each field dict: {key, kind: toggle|select|segmented|number|text, label,
    # description?, default, options?: [{value,label}], min?, max?, step?,
    # unit?, placeholder?, max_length?}. Labels are raw strings — the plugin
    # owns its copy. First-party apps use the OS-side descriptor registry
    # instead (i18n keys + client-side option loaders); this field exists so a
    # plugin gets a settings page without shipping surface code.
    settings_schema: list[dict[str, Any]] | None = None

    def to_dict(self, *, capabilities: list[str] | None = None,
                enabled: bool | None = None) -> dict[str, Any]:
        locked = self.capability is not None and (
            capabilities is not None and self.capability not in capabilities
        )
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "kind": self.kind.value,
            "category": self.category,
            "icon": self.icon,
            "capability": self.capability,
            "min_tier": self.min_tier,
            "surfaces": list(self.surfaces),
            "requires": list(self.requires),
            "dev_only": self.dev_only,
            "source": self.source,
            "scope": self.scope,
            "app_category": self.app_category,
            "hidden": self.hidden,
            "merged_into": self.merged_into,
            "about": self.about,
            "features": self.features,
            "settings_schema": self.settings_schema,
            "locked": locked,
            "default_enabled": self.default_enabled,
            "enabled": self.default_enabled if enabled is None else enabled,
        }


# ── Built-in operator apps (was the hardcoded os `MODULES` array) ────────────
# category order for surfaces: operate · grow · build · tools · system
CATEGORY_ORDER = ["operate", "grow", "build", "tools", "system"]
CATEGORY_LABELS = {
    "operate": "Operate", "grow": "Grow", "build": "Build",
    "tools": "Tools", "system": "System",
}

# The user-facing APPS grouping (desktop sidebar sections + Bay filters) — an
# orthogonal axis to the settings taxonomy above. Locked with the operator
# 2026-07-09 ("Plain & sharp"). An app with app_category=None is settings-only.
APP_CATEGORY_ORDER = ["Money", "Comms", "Create", "Life", "Systems", "Knowledge", "Security"]

BUILTIN_MODULES: list[ModuleDef] = [
    ModuleDef(
        id="finance", label="Finance", description="Money, invoices & bizops.",
        kind=ModuleKind.APP, category="operate", icon="wallet",
        capability="business_ops", min_tier="plus",
        surfaces=["os-tile:finance", "cli:finance"],  # deck code deleted 2026-07-12 (deck→OS)
        requires=["gateway"], default_enabled=True, app_category="Money",
        scope="space",  # per-space BOOKS (locked rule #6): the ledger follows the space
    ),
    ModuleDef(
        id="devops", label="Deploys", description="Servers, deploys & infra.",
        kind=ModuleKind.APP, category="operate", icon="server",
        capability="deploy_ops", min_tier="plus",
        surfaces=["os-tile:devops", "cli:hosts"],  # deck code deleted 2026-07-12 (deck→OS)
        requires=["gateway"], default_enabled=False, app_category="Systems",
    ),
    ModuleDef(
        id="goals", label="Goals", description="Milestones & progress.",
        kind=ModuleKind.APP, category="grow", icon="target",
        capability=None,
        surfaces=["os-tile:goals"],  # deck code deleted 2026-07-12 (deck→OS)
        requires=["gateway"], default_enabled=True, app_category="Life",
    ),
    ModuleDef(
        id="life", label="Life", description="Habits & streaks.",
        kind=ModuleKind.APP, category="grow", icon="heart-pulse",
        capability=None,
        surfaces=["os-tile:life"],  # deck code deleted 2026-07-12 (deck→OS)
        requires=["gateway"], default_enabled=True, app_category="Life",
    ),
    ModuleDef(
        id="projects", label="Projects", description="Build apps & projects with AI.",
        kind=ModuleKind.APP, category="build", icon="hammer",
        capability="ai_operator", min_tier="plus",
        surfaces=["os-tile:projects"],  # deck code deleted 2026-07-12 (deck→OS)
        requires=["gateway"], default_enabled=False, app_category="Money",
        scope="space",  # projects live in the space's book
    ),
    # ── primitives (core depends on them; always available) ──────────────────
    ModuleDef(
        id="vault", label="Vault", description="Encrypted secrets & credential store.",
        kind=ModuleKind.PRIMITIVE, category="system", icon="box",
        capability=None,
        surfaces=["cli:vault", "deck-section:vault", "os-tile:vault"],
        requires=[], default_enabled=True, source="builtin", app_category="Security",
    ),
    # ── Wave-2: free, toggleable feature modules (in-place; not yet extracted).
    # capability=None → always available; user can switch each off in the registry
    # (surfaces gated with requires_module where they have deck routes).
    # NOTE: "email" moved to the navig-email plugin (registers itself via
    # register_module); "generate" likewise via navig-generate. Extracted modules are
    # NOT listed here — they appear only when their package is installed.
    ModuleDef(
        id="personas", label="Personas", description="AI personas & reply styles.",
        kind=ModuleKind.APP, category="build", icon="drama",
        capability=None,
        surfaces=["deck-section:persona"],
        requires=["gateway"], default_enabled=True,
    ),
    ModuleDef(
        id="missions", label="Missions", description="Long-running goals & missions.",
        kind=ModuleKind.APP, category="grow", icon="flag",
        capability=None,
        surfaces=[], requires=[], default_enabled=True,
    ),
    ModuleDef(
        id="webhooks", label="Webhooks", description="Inbound webhook endpoints & routing.",
        kind=ModuleKind.SERVICE, category="system", icon="webhook",
        capability=None,
        surfaces=["cli:webhook"], requires=["gateway"], default_enabled=True,
    ),
    ModuleDef(
        id="blackbox", label="Blackbox", description="Session recorder & diagnostics.",
        kind=ModuleKind.SERVICE, category="system", icon="box",
        capability=None,
        surfaces=["cli:blackbox"], requires=[], default_enabled=True,
    ),
    # ── Deck data-surface apps (deck→desktop migration, locked 2026-07-09) ────
    # The deck's APP_CARDS catalog promoted into the registry so ALL surfaces
    # render one catalog. Desktop eligibility = an "os-tile:<id>" surface entry;
    # telegram + wallet are deliberately deck-only (Telegram Mini App runtime).
    # "email"/"generate" are plugin-registered (navig-email / navig-generate).
    # Money
    ModuleDef(
        id="wallet", label="Wallet", description="TON + bank wallet (Telegram-native).",
        kind=ModuleKind.APP, category="operate", icon="gem",
        capability=None,
        surfaces=["deck-section:wallet"],  # deck-ONLY: TON connect needs the TMA
        requires=["gateway"], default_enabled=True, app_category="Money",
    ),
    # Comms
    ModuleDef(
        id="messages", label="Messages", description="Chats & contacts across channels.",
        kind=ModuleKind.APP, category="operate", icon="message-square",
        capability=None,
        surfaces=["os-tile:messages"],  # deck code deleted 2026-07-12 (deck→OS)
        requires=["gateway"], default_enabled=True, app_category="Comms",
    ),
    ModuleDef(
        id="telegram", label="Telegram", description="Manage rooms, catalog media, post & search.",
        kind=ModuleKind.APP, category="operate", icon="send",
        capability=None,
        surfaces=["deck-section:telegram"],  # deck-ONLY: MTProto manager is TMA-coupled
        requires=["gateway"], default_enabled=True, app_category="Comms",
    ),
    ModuleDef(
        id="contacts", label="Contacts", description="People across your networks.",
        kind=ModuleKind.APP, category="operate", icon="users",
        capability=None,
        # Deck code deleted 2026-07-12 (deck→OS); renders as the Contacts tab
        # inside the desktop Messages app (merged_into keeps deep-links alive).
        surfaces=[],
        requires=["gateway"], default_enabled=True, app_category="Comms",
        hidden=True, merged_into="messages",
    ),
    # Create
    ModuleDef(
        id="studio", label="Studio", description="Compose, schedule & publish across networks.",
        kind=ModuleKind.APP, category="grow", icon="sparkles",
        capability=None,
        surfaces=["os-tile:studio"],  # deck code deleted 2026-07-12 (deck→OS)
        requires=["gateway"], default_enabled=True, app_category="Create",
    ),
    # Life
    ModuleDef(
        id="tasks", label="Tasks", description="Goals & AI task board.",
        kind=ModuleKind.APP, category="grow", icon="list-checks",
        capability=None,
        surfaces=["os-tile:tasks"],  # deck code deleted 2026-07-12 (deck→OS)
        requires=["gateway"], default_enabled=True, app_category="Life",
    ),
    ModuleDef(
        id="schedule", label="Schedule", description="Calendar & reminders.",
        kind=ModuleKind.APP, category="grow", icon="clock",
        capability=None,
        surfaces=[],  # deck code deleted 2026-07-12; OS Life app hosts the tab
        requires=["gateway"], default_enabled=True, app_category="Life",
        hidden=True, merged_into="life",
    ),
    ModuleDef(
        id="health", label="Health", description="Habits, streaks & vitals.",
        kind=ModuleKind.APP, category="grow", icon="activity",
        capability=None,
        surfaces=[],  # deck code deleted 2026-07-12; OS Life app hosts the tab
        requires=["gateway"], default_enabled=True, app_category="Life",
        hidden=True, merged_into="life",
    ),
    # Systems
    ModuleDef(
        id="system", label="System", description="Live CPU, RAM, services & ports.",
        kind=ModuleKind.APP, category="system", icon="monitor",
        capability=None,
        surfaces=["os-tile:system"],  # deck code deleted 2026-07-12 (deck→OS)
        requires=["gateway"], default_enabled=True, app_category="Systems",
    ),
    ModuleDef(
        id="remote", label="Remote", description="SSH hosts, files, shell & docker.",
        kind=ModuleKind.APP, category="operate", icon="terminal",
        capability=None,
        surfaces=["os-tile:remote"],  # deck code deleted 2026-07-12 (deck→OS)
        requires=["gateway"], default_enabled=True, app_category="Systems",
    ),
    ModuleDef(
        id="database", label="Database", description="Schemas & SQL runner.",
        kind=ModuleKind.APP, category="operate", icon="database",
        capability=None,
        surfaces=["os-tile:database"],  # deck code deleted 2026-07-12 (deck→OS)
        requires=["gateway"], default_enabled=True, app_category="Systems",
    ),
    ModuleDef(
        id="nettools", label="NetTools", description="DNS, SSL, WHOIS & network checks.",
        kind=ModuleKind.APP, category="tools", icon="globe",
        capability=None,
        surfaces=["os-tile:nettools"],  # deck code deleted 2026-07-12 (deck→OS)
        requires=["gateway"], default_enabled=True, app_category="Systems",
    ),
    ModuleDef(
        id="connections", label="Connections", description="AI providers & accounts.",
        kind=ModuleKind.APP, category="system", icon="plug",
        capability=None,
        surfaces=["os-tile:connections"],  # deck code deleted 2026-07-12 (deck→OS)
        requires=["gateway"], default_enabled=True, app_category="Systems",
    ),
    ModuleDef(
        id="mesh", label="Mesh", description="LAN peers, formations & the Council.",
        kind=ModuleKind.APP, category="system", icon="network",
        capability=None,
        # Desktop-first (slice B6): the deck renders mesh via its own nav-level
        # Mesh Explorer section, not an app card — so no deck-section surface.
        surfaces=["os-tile:mesh"],
        requires=["gateway"], default_enabled=True, app_category="Systems",
    ),
    # Knowledge
    ModuleDef(
        id="context", label="Context", description="Memory bank & working context.",
        kind=ModuleKind.APP, category="system", icon="brain",
        capability=None,
        # Deck TILE deleted 2026-07-12; the deck still embeds Context in
        # Settings → Context, so the deck-section surface remains truthful.
        surfaces=["os-tile:context", "deck-section:context"],
        requires=["gateway"], default_enabled=True, app_category="Knowledge",
    ),
    ModuleDef(
        id="inbox", label="Inbox", description="Approvals, asks & document routing.",
        kind=ModuleKind.APP, category="operate", icon="inbox",
        capability=None,
        # Deck TILE deleted 2026-07-12; the deck keeps its Inbox TAB
        # (sections/inbox-section.tsx — asks/approvals + drop-upload), so the
        # deck-section surface remains truthful.
        surfaces=["os-tile:inbox", "deck-section:inbox"],
        requires=["gateway"], default_enabled=True, app_category="Knowledge",
    ),
    ModuleDef(
        id="spaces", label="Spaces", description="Workshops — install, enable, switch.",
        kind=ModuleKind.APP, category="system", icon="layers",
        capability=None,
        surfaces=["os-tile:spaces"],  # deck code deleted 2026-07-12; Bay covers installs
        requires=["gateway"], default_enabled=True, app_category="Knowledge",
    ),
    ModuleDef(
        id="knowledge", label="Knowledge", description="Notes, bookmarks & links.",
        kind=ModuleKind.APP, category="grow", icon="book-open",
        capability=None,
        surfaces=["os-tile:knowledge"],  # deck code deleted 2026-07-12 (deck→OS)
        requires=["gateway"], default_enabled=True, app_category="Knowledge",
    ),
    ModuleDef(
        id="skills", label="Skills", description="Agent skills installed on this brain.",
        kind=ModuleKind.APP, category="build", icon="wrench",
        capability=None,
        # No surfaces AND no app_category: the desktop renders skills through
        # its NATIVE Skills navigator (not a catalog tile) and the deck viewer
        # was deleted 2026-07-12 (deck→OS). CLI: `navig skills`. The module
        # stays toggleable in Settings → Modules.
        surfaces=[],
        requires=["gateway"], default_enabled=True,
    ),
    # Security
    ModuleDef(
        id="passport", label="Passport", description="Identity & credentials.",
        kind=ModuleKind.APP, category="system", icon="id-card",
        capability="security_ops", min_tier="plus",
        # Deck APP tile deleted 2026-07-12 (deck→OS); the deck passport TAB
        # (sections/passport-section.tsx) is a nav section, not this app tile.
        surfaces=["os-tile:passport"],
        requires=["gateway"], default_enabled=True, app_category="Security",
    ),
]


class ModuleRegistry:
    """Discovers modules from built-ins + installed plugins (+ launchers via
    `navig.module.json`) and resolves their locked/enabled state against the
    current Harbor entitlement + user overrides."""

    def __init__(self) -> None:
        self._defs: dict[str, ModuleDef] = {}
        self._discovered = False

    # ── discovery ───────────────────────────────────────────────────────────
    def discover(self) -> "ModuleRegistry":
        self._defs = {m.id: m for m in BUILTIN_MODULES}
        # Make sure pip plugins' register() has run even OUTSIDE gateway boot
        # (plain CLI `navig modules` / `navig store`), so their ModuleDefs land
        # in _EXTERNAL_DEFS before we apply it.
        _load_entry_point_plugins_once()
        # Modules contributed by installed pip plugins (via register_module()),
        # e.g. navig-generate registers its "generate" module at gateway boot. Applied
        # over built-ins so a plugin can override, and survives rediscovery.
        for m in _EXTERNAL_DEFS:
            self._defs[m.id] = m
        self._discover_plugins()
        self._discover_launchers()
        self._discovered = True
        return self

    def _ensure(self) -> None:
        if not self._discovered:
            self.discover()

    def _discover_plugins(self) -> None:
        """Surface installed CC/NAVIG plugins as capability modules (best-effort)."""
        try:
            from navig.core import Config

            plugins_dir = Config().plugins_dir
        except Exception:  # noqa: BLE001
            return
        if not plugins_dir.exists():
            return
        from navig.plugins.package import load_package

        for child in sorted(plugins_dir.iterdir()):
            if not child.is_dir():
                continue
            if not ((child / ".claude-plugin" / "plugin.json").exists()
                    or (child / "plugin.json").exists()):
                continue
            try:
                pkg = load_package(child)
            except Exception as exc:  # noqa: BLE001 — a bad plugin never breaks the registry
                logger.warning("module registry: plugin %s failed to load: %s", child.name, exc)
                continue
            mid = f"plugin:{pkg.plugin_id}"
            # Paid plugins declare a `capability` in plugin.json (canonical
            # module flag or a Harbor Bay `item:<id>` grant) → the standard
            # locked computation applies, giving installed-but-lapsed plugins
            # the Game-Pass "moored until you return" behavior.
            manifest_cap = pkg.manifest.get("capability")
            navig_block = pkg.manifest.get("navig") if isinstance(pkg.manifest.get("navig"), dict) else {}
            capability = str(manifest_cap or navig_block.get("capability") or "") or None
            self._defs[mid] = ModuleDef(
                id=mid,
                label=str(pkg.manifest.get("name") or pkg.plugin_id),
                description=str(pkg.manifest.get("description") or "Installed plugin."),
                kind=ModuleKind.PLUGIN, category="tools", icon="puzzle",
                capability=capability,
                surfaces=["cli:plugin"], source="plugin",
                default_enabled=pkg.health.is_usable,
            )

    def _discover_launchers(self) -> None:
        """Discover launcher/primitive modules that ship a `navig.module.json`.

        Scans the plugins dir (where standalone tools may drop a manifest); the
        canonical launchers (navig-menu) also register here once retrofitted.
        Best-effort — never raises.
        """
        try:
            from navig.core import Config

            plugins_dir = Config().plugins_dir
        except Exception:  # noqa: BLE001
            return
        if not plugins_dir.exists():
            return
        import json

        for manifest in sorted(plugins_dir.glob("*/navig.module.json")):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("module registry: bad module manifest %s: %s", manifest, exc)
                continue
            mid = str(data.get("id") or manifest.parent.name)
            try:
                kind = ModuleKind(str(data.get("kind", "launcher")))
            except ValueError:
                kind = ModuleKind.LAUNCHER
            self._defs[mid] = ModuleDef(
                id=mid,
                label=str(data.get("name") or mid),
                description=str(data.get("description") or ""),
                kind=kind, category=str(data.get("category", "tools")),
                icon=str(data.get("icon", "box")),
                capability=data.get("capability"),
                min_tier=data.get("tier"),
                surfaces=list(data.get("surfaces", [])),
                requires=list(data.get("requires", [])),
                source=str(data.get("source", "launcher")),
            )

    # ── state resolution ────────────────────────────────────────────────────
    def _overrides(self) -> dict[str, bool]:
        try:
            from navig.core import Config

            val = Config().get(_OVERRIDES_KEY, {})
            return {str(k): bool(v) for k, v in (val or {}).items()} if isinstance(val, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _capabilities(self) -> list[str]:
        try:
            from navig.license import current_status

            return list(current_status().capabilities)
        except Exception:  # noqa: BLE001
            return ["core_ops"]

    def get(self, module_id: str) -> ModuleDef | None:
        self._ensure()
        return self._defs.get(module_id)

    def is_enabled(self, module_id: str) -> bool:
        d = self.get(module_id)
        if d is None:
            return False
        return self._overrides().get(module_id, d.default_enabled)

    def set_enabled(self, module_id: str, enabled: bool) -> bool:
        """Persist a user override. Returns False for an unknown module."""
        self._ensure()
        if module_id not in self._defs:
            return False
        from navig.core import Config

        overrides = self._overrides()
        overrides[module_id] = bool(enabled)
        cfg = Config()
        cfg.set(_OVERRIDES_KEY, overrides, scope="global")
        # set() only mutates the in-memory copy — without save() the override
        # silently reverts on daemon restart.
        cfg.save(scope="global")
        return True

    def list_modules(self, *, include_dev: bool = False) -> list[dict[str, Any]]:
        """Full catalog with resolved `locked` + `enabled` — the shape surfaces render."""
        self._ensure()
        caps = self._capabilities()
        overrides = self._overrides()
        out: list[dict[str, Any]] = []
        for d in self._defs.values():
            if d.dev_only and not include_dev:
                continue
            enabled = overrides.get(d.id, d.default_enabled)
            out.append(d.to_dict(capabilities=caps, enabled=enabled))
        out.sort(key=lambda m: (CATEGORY_ORDER.index(m["category"])
                                if m["category"] in CATEGORY_ORDER else 99, m["label"].lower()))
        return out


_REGISTRY: ModuleRegistry | None = None

# Modules contributed by installed pip plugins (see register_module). Kept at
# module scope so they survive `ModuleRegistry.discover()` rediscovery.
_EXTERNAL_DEFS: list[ModuleDef] = []

_EP_PLUGINS_LOADED = False


def _load_entry_point_plugins_once() -> None:
    """Run every `navig.plugins` entry point's register() (idempotent, best-effort).

    At gateway boot `load_entry_point_plugins()` runs anyway; this makes the
    same registrations happen for plain-CLI registry consumers. Plugins'
    register() must be idempotent (the media plugins guard with a module flag), and
    registering the `gateway:register_routes` hook in a CLI process is harmless
    — the hook never fires without a gateway. Never runs on the CLI fast path:
    only a registry touch (`navig modules` / `navig store` / deck) gets here.
    """
    global _EP_PLUGINS_LOADED
    if _EP_PLUGINS_LOADED:
        return
    _EP_PLUGINS_LOADED = True
    try:
        from navig.core.plugins import load_entry_point_plugins

        load_entry_point_plugins()
    except Exception as exc:  # noqa: BLE001 — a broken plugin never breaks the registry
        logger.debug("entry-point plugin load skipped: %s", exc)


def reset_entry_points() -> None:
    """Forget the entry-point once-guard so the NEXT ``discover()`` re-runs
    plugin registration (`_load_entry_point_plugins_once`).

    Called by the install handlers (Bay/deck/store) after a successful install,
    so a freshly pip-installed plugin's module appears on the next catalog GET
    — no daemon restart. ``importlib.invalidate_caches()`` makes the metadata
    finder see distributions installed after process start. Safe to call any
    time: plugins' ``register()`` must already be idempotent (see
    `_load_entry_point_plugins_once`), and ``discover()`` rebuilds per request.
    """
    global _EP_PLUGINS_LOADED
    _EP_PLUGINS_LOADED = False
    try:
        import importlib  # noqa: PLC0415

        importlib.invalidate_caches()
    except Exception:  # noqa: BLE001 — cache invalidation is best-effort
        pass


def register_module(module: ModuleDef) -> None:
    """Register a plugin-provided module in the catalog (idempotent by id).

    Called from a plugin's ``register()`` at gateway boot (mirrors how
    ``navig-harbor`` wires its routes). Appears immediately even if the registry
    singleton was already discovered, AND is re-applied on any later rediscovery.
    """
    _EXTERNAL_DEFS[:] = [m for m in _EXTERNAL_DEFS if m.id != module.id] + [module]
    if _REGISTRY is not None:
        _REGISTRY._defs[module.id] = module


def get_registry() -> ModuleRegistry:
    """Process-wide registry (rediscovered lazily). Call `.discover()` to refresh."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ModuleRegistry().discover()
    return _REGISTRY
