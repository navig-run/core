"""
NAVIG CLI Command Registration
===============================

Lazy command registration for external modules.  Extracted from
``cli/__init__.py`` to reduce complexity (P1-14 CLI decomposition).

Responsibilities:
  - ``_EXTERNAL_CMD_MAP``: mapping of CLI name → (module_path, attr_name)
  - ``_register_external_commands()``: register sub-apps on the Typer app
  - ``extract_non_global_tokens()``: strip global flags from argv for skip-checks
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

if TYPE_CHECKING:
    import typer

logger = logging.getLogger(__name__)

# =============================================================================
# External command map — name → (module_path, attr_name)
# =============================================================================

_EXTERNAL_CMD_MAP: dict[str, tuple[str, str]] = {
    "bridge": ("navig.commands.bridge", "bridge_app"),
    # github now ships in the navig-github plugin via the `navig.commands`
    # entry-point group (discovered by _entry_point_commands()). No hardcoded entry.
    # tiktok / tt now ship in the navig-download plugin via the `navig.commands`
    # entry-point group (discovered by _entry_point_commands()). No hardcoded entry.
    # blackbox / bb: the recorder capability (capability_registry OPTIONAL) — was
    # declared but never wired into the CLI; register it so `navig blackbox` works.
    "blackbox": ("navig.commands.blackbox", "blackbox_app"),
    "bb": ("navig.commands.blackbox", "blackbox_app"),
    "copilot": ("navig.commands.ask", "copilot_app"),
    # generate: analyse (video→briefing) + AI generate (image/video/audio). Canonical
    # name; `media` is a DEPRECATED alias — both invoke media_app and the app callback
    # warns when run as `media`. The navig-download plugin adds top-level `download`/
    # `tiktok` verbs via its own entry-point group.
    "generate": ("navig.commands.media", "media_app"),
    "media": ("navig.commands.media", "media_app"),
    "inbox": ("navig.commands.inbox", "inbox_app"),
    "sync": ("navig.commands.sync", "sync_app"),
    "agent": ("navig.commands.agent", "agent_app"),
    "continuation": ("navig.commands.agent", "continuation_app"),
    "service": ("navig.commands.service", "service_app"),
    "stack": ("navig.commands.stack", "stack_app"),
    "tray": ("navig.commands.tray", "tray_app"),
    "formation": ("navig.commands.formation", "formation_app"),
    "council": ("navig.commands.council", "council_app"),
    "auto": ("navig.commands.auto", "auto_app"),
    "cdp": ("navig.commands.cdp", "cdp_app"),
    "do": ("navig.commands.do", "do_app"),
    "gmail": ("navig.commands.gmail", "gmail_app"),
    "evolve": ("navig.commands.evolution", "evolution_app"),
    "script": ("navig.commands.script", "script_app"),
    # calendar now ships in the navig-calendar plugin via the `navig.commands`
    # entry-point group. Providers stay in core (navig.agent.proactive).
    "mode": ("navig.commands.mode", "mode_app"),
    # email now ships in the navig-email plugin via the `navig.commands` entry-point.
    "voice": ("navig.commands.voice", "voice_app"),
    "crash": ("navig.commands.crash", "crash_app"),
    "telegram": ("navig.commands.telegram", "telegram_app"),
    "tg": ("navig.commands.telegram", "telegram_app"),        # hidden alias
    "matrix": ("navig.commands.matrix", "matrix_app"),
    "mx": ("navig.commands.matrix", "matrix_app"),            # hidden alias
    # `store` = the hub for everything connectable ("doctor of wiring").
    # SQLite maintenance (the old `navig store`) moved to `navig db local`;
    # hidden forwards live inside the store app for one release.
    "store": ("navig.commands.store", "store_app"),
    "hub": ("navig.commands.store", "store_app"),  # hidden alias
    "vault": ("navig.commands.vault", "vault_app"),
    "cred": ("navig.commands.vault", "cred_app"),             # deprecated → navig vault
    "cred-profile": ("navig.commands.vault", "profile_app"),  # deprecated → navig vault profile
    "profile": ("navig.commands.profile", "profile_app"),
    "flux": ("navig.commands.flux", "flux_app"),
    "fx": ("navig.commands.flux", "flux_app"),                # hidden alias
    "ai": ("navig.commands.ai", "ai_app"),
    "connect": ("navig.commands.connect_cmd", "connect_app"),
    "connections": ("navig.commands.connect_cmd", "connect_app"),  # hidden alias
    "brain": ("navig.commands.brain", "brain_app"),
    "backup": ("navig.commands.backup", "backup_app"),
    "tunnel": ("navig.commands.tunnel", "tunnel_app"),
    "t": ("navig.commands.tunnel", "tunnel_app"),             # hidden alias
    "skills": ("navig.commands.skills", "skills_app"),
    "skill": ("navig.commands.skills", "skills_app"),
    "plugin": ("navig.commands.plugin", "plugin_app"),
    "plugins": ("navig.commands.plugin", "plugin_app"),
    "modules": ("navig.commands.modules_cmd", "modules_app"),
    "module": ("navig.commands.modules_cmd", "modules_app"),   # singular alias
    "history": ("navig.commands.history", "history_app"),
    "hist": ("navig.commands.history", "history_app"),        # hidden alias
    "ledger": ("navig.commands.ledger", "ledger_app"),
    "audit": ("navig.commands.audit", "audit_app"),
    "trigger": ("navig.commands.triggers", "trigger_app"),
    "insights": ("navig.commands.insights", "insights_app"),
    "server-template": ("navig.commands.server_template", "server_template_app"),
    "gateway": ("navig.commands.gateway", "gateway_app"),
    "bot": ("navig.commands.gateway", "bot_app"),
    "heartbeat": ("navig.commands.gateway", "heartbeat_app"),
    "approve": ("navig.commands.gateway", "approve_app"),
    "queue": ("navig.commands.gateway", "queue_app"),
    "task": ("navig.commands.workflow", "task_app"),
    "install": ("navig.commands.install", "install_app"),
    "block": ("navig.commands.block", "block_app"),
    "quick": ("navig.commands.suggest", "quick_app"),
    "q": ("navig.commands.suggest", "quick_app"),             # hidden alias
    "hosts": ("navig.commands.local", "hosts_app"),
    "cortex": ("navig.commands.cortex", "cortex_app"),
    "desktop": ("navig.commands.desktop", "desktop_app"),
    "net": ("navig.commands.net", "net_app"),
    "host": ("navig.commands.host", "host_app"),
    "h": ("navig.commands.host", "host_app"),                 # hidden alias
    "context": ("navig.commands.context", "context_app"),
    "ctx": ("navig.commands.context", "context_app"),         # hidden alias
    "index": ("navig.commands.index", "index_app"),
    "flow": ("navig.commands.flow", "flow_app"),
    "scaffold": ("navig.commands.scaffold", "scaffold_app"),
    "app": ("navig.commands.app", "app_app"),
    "a": ("navig.commands.app", "app_app"),                   # hidden alias
    "file": ("navig.commands.files", "file_app"),
    "f": ("navig.commands.files", "file_app"),                # hidden alias
    "log": ("navig.commands.log", "log_app"),
    "logs": ("navig.commands.log", "log_app"),
    "l": ("navig.commands.log", "log_app"),                   # hidden alias
    "local": ("navig.commands.local", "local_app"),
    "software": ("navig.commands.local", "software_app"),
    "config": ("navig.commands.config", "config_app"),
    "web": ("navig.commands.webserver", "web_app"),
    "stats": ("navig.commands.stats", "stats_app"),
    "health": ("navig.commands.stack", "stack_app"),
    # Compatibility aliases
    "cert": ("navig.commands.webserver", "web_app"),
    "key": ("navig.commands.host", "host_app"),
    "firewall": ("navig.commands.local", "local_app"),
    "dns": ("navig.commands.local", "local_app"),
    "port": ("navig.commands.local", "local_app"),
    "proxy": ("navig.commands.tunnel", "tunnel_app"),
    "env": ("navig.commands.config", "config_app"),
    "secret": ("navig.commands.vault", "vault_app"),          # deprecated → navig vault
    "job": ("navig.commands.flow", "flow_app"),
    "wiki": ("navig.commands.wiki", "wiki_app"),
    "alias": ("navig.commands.script", "script_app"),
    "server": ("navig.commands.server", "server_app"),
    "s": ("navig.commands.server", "server_app"),             # hidden alias
    "db": ("navig.commands.db", "db_app"),
    "database": ("navig.commands.db", "db_app"),
    # Both of these were fully implemented Typer apps that nothing ever
    # registered — so `navig deploy init` (which deploy.py's OWN error message
    # tells you to run) and `navig action list` (likewise) did not exist.
    "deploy": ("navig.commands.deploy", "deploy_app"),
    "action": ("navig.commands.action", "action_app"),
    "cost": ("navig.commands.cost", "cost_app"),
    "output-style": ("navig.commands.output_style", "output_style_app"),
    "links": ("navig.commands.links", "links_app"),
    "kg": ("navig.commands.kg", "kg_app"),
    "knowledge": ("navig.commands.kg", "kg_app"),
    "webhook": ("navig.commands.webhook", "webhook_app"),
    "webhooks": ("navig.commands.webhook", "webhook_app"),
    "signals": ("navig.commands.signals", "signals_app"),
    "signal": ("navig.commands.signals", "signals_app"),          # alias
    "cron": ("navig.commands.cron", "cron_app"),
    "habit": ("navig.commands.habit", "habit_app"),
    "habits": ("navig.commands.habit", "habit_app"),             # alias
    "day": ("navig.commands.life_dashboard", "life_dashboard_app"),
    "life": ("navig.commands.life_dashboard", "life_dashboard_app"),
    "doctor": ("navig.commands.doctor", "doctor_app"),
    "docker": ("navig.commands.docker", "docker_app"),
    "prompts": ("navig.commands.prompts", "prompts_app"),
    "browser": ("navig.commands.browser", "browser_app"),
    # cdp/do/gmail (auto-login browser surfaces) are registered once above, near "auto".
    "import": ("navig.commands.import_cmd", "import_app"),
    "dispatch": ("navig.commands.dispatch", "dispatch_app"),
    "contacts": ("navig.commands.dispatch", "contacts_app"),
    "ct": ("navig.commands.dispatch", "contacts_app"),        # hidden alias
    "paths": ("navig.commands.paths_cmd", "paths_app"),
    "mcp": ("navig.commands.mcp_cmd", "mcp_app"),
    "radar": ("navig.commands.radar", "radar_app"),
    "watch": ("navig.commands.watch_cmd", "watch_app"),
    "mesh": ("navig.commands.mesh", "mesh_app"),
    "debug": ("navig.commands.debug_cmd", "debug_app"),
    "spaces": ("navig.commands.space", "space_app"),
    "telemetry": ("navig.commands.telemetry", "telemetry_app"),
    "wut": ("navig.commands.wut", "app"),
    "eval": ("navig.commands.eval", "app"),
    "agents": ("navig.commands.agents", "app"),
    "webdash": ("navig.commands.webdash", "app"),
    "snapshot": ("navig.commands.snapshot", "app"),
    "replay": ("navig.commands.replay", "app"),
    "cloud": ("navig.commands.cloud", "app"),
    "repo": ("navig.commands.repo", "repo_app"),
    "lighthouse": ("navig.commands.lighthouse", "app"),
    "license": ("navig.commands.license", "app"),
    "miniapp": ("navig.commands.miniapp", "app"),
    "benchmark": ("navig.commands.benchmark", "app"),
    "finance": ("navig.commands.finance", "finance_app"),
    "work": ("navig.commands.work", "work_app"),
    "plans": ("navig.commands.plans", "plans_app"),
    "plan": ("navig.commands.plan_mode", "app"),
    "origin": ("navig.commands.origin", "origin_app"),
    "user": ("navig.commands.user", "user_app"),
    "node": ("navig.commands.node", "node_app"),
    "boot": ("navig.commands.boot_cmd", "boot_app"),
    "space": ("navig.commands.space", "space_app"),
    "blueprint": ("navig.commands.blueprint", "blueprint_app"),
    "deck": ("navig.commands.deck", "deck_app"),
    "portable": ("navig.commands.portable", "portable_app"),
    "migrate": ("navig.commands.migrate", "migrate_app"),
    "system": ("navig.commands.system_cmd", "system_app"),
    "mount": ("navig.commands.mount", "mount_app"),
    "update": ("navig.commands.update", "update_app"),
    "proactive": ("navig.commands.proactive", "proactive_app"),
    "memory": ("navig.commands.memory", "memory_app"),
    "connector": ("navig.commands.connector_cmd", "connector_app"),
    "menu": ("navig.commands.menu", "menu_app"),
    "dev": ("navig.commands.menu", "dev_app"),       # shortcut → navig-menu run dev
    "build": ("navig.commands.menu", "build_app"),   # shortcut → navig-menu run build
    "test": ("navig.commands.menu", "test_app"),     # shortcut → navig-menu run test
}

# Hiding a command from `--help` and declaring it non-public are TWO DIFFERENT THINGS,
# and conflating them silently deleted real commands from the docs.
#
# `navig.registry.manifest` derives a command's status from its Typer `hidden` flag, and
# `build_public_manifest()` drops anything hidden. That manifest is not just cosmetic — it
# feeds `navig help`, the deck's command schema (`/api/deck/cli`), navig.run/commands, and
# every agent that asks navig what commands exist. So a name parked in the old single
# `_HIDDEN_COMMANDS` set to de-clutter `--help` also became undiscoverable: `navig ai`
# (12 cmds), `navig brain` (which navig-bridge itself shells out to), and `navig cost` were
# all live, unique capabilities that no other name served, and all three were invisible.
#
# Hence two sets. `--help` hides the union (unchanged); only ALIASES leave the manifest.

# Duplicate names for a group ALSO registered under a canonical, visible name — verified
# by comparing the backing Typer instance, not by eyeballing. The canonical command
# documents every subcommand, so publishing these is pure duplication.
_ALIAS_COMMANDS: frozenset[str] = frozenset({
    # Short aliases
    "tg", "mx", "fx", "h", "a", "f", "l", "s", "t", "q",
    # Long aliases → canonical
    "database",     # → db
    "hist",         # → history
    "ctx",          # → context
    "ct",           # → contacts
    "connections",  # → connect
    "hub",          # → store
    "cred",         # → vault
    "secret",       # → vault
    "alias",        # → script
    "health",       # → stack
    "cert",         # → web
    "key",          # → host
    "firewall",     # → local
    "dns",          # → local
    "port",         # → local
    "proxy",        # → tunnel
    "env",          # → config
    "job",          # → flow
    "habits",       # → habit
    "day",          # → life
    # Deprecated, replaced by a SUBcommand (so no visible top-level group serves it).
    "cred-profile",  # → navig vault profile
})

# Real, unique capabilities. Nothing else serves them, so they are PUBLIC API and must
# stay in the manifest — they are merely unlisted from `--help` to keep the top-level
# command list readable.
_UNLISTED_COMMANDS: frozenset[str] = frozenset({
    "ai", "brain", "cost", "continuation",
    "hosts", "software", "output-style",
    "habit", "life",
})

# What `--help` hides. Derived, so the two sets above cannot drift out of sync with it.
_HIDDEN_COMMANDS: frozenset[str] = _ALIAS_COMMANDS | _UNLISTED_COMMANDS

# =============================================================================
# Global-flag stripping
# =============================================================================

# Flags that consume the following token as their value.
_VALUE_CONSUMING_GLOBAL_FLAGS: frozenset[str] = frozenset({"--host", "-h", "--app", "-p"})

# All top-level global flags (value-consuming and bare).
_GLOBAL_FLAGS: frozenset[str] = frozenset({
    "--host", "-h",
    "--app", "-p",
    "--verbose",
    "--quiet", "-q",
    "--dry-run",
    "--yes", "-y",
    "--confirm", "-c",
    "--raw",
    "--json",
    "--debug-log",
    "--no-cache",
    "--version", "-v",
    "--help",
})


def extract_non_global_tokens(args: list[str]) -> list[str]:
    """Return *args* with global flags and their consumed values stripped.

    Used by skip-lists in middleware and fact-extraction to examine only the
    actual command tokens without being confused by flag values like
    ``--host myserver`` (where ``myserver`` must not be treated as a command).
    """
    tokens: list[str] = []
    skip_next = False
    for token in args:
        if skip_next:
            skip_next = False
            continue
        if token in _VALUE_CONSUMING_GLOBAL_FLAGS:
            skip_next = True
            continue
        if token in _GLOBAL_FLAGS or token.startswith("--"):
            continue
        tokens.append(token)
    return tokens


def _first_non_global_token(args: list[str]) -> str | None:
    """Return the first non-global token from *args*, or ``None``."""
    tokens = extract_non_global_tokens(args)
    return tokens[0] if tokens else None


def _resolve_cli_target_from_argv(argv: list[str] | None = None) -> str | None:
    """Resolve the top-level CLI command name from *argv*.

    Returns ``None`` when:
    - argv is empty or has no subcommand.
    - argv[0] does not look like ``navig`` (e.g. when called inside pytest).

    The sentinel check prevents misrouting in embedded / test contexts where
    ``sys.argv[0]`` is ``pytest`` or ``python``.
    """
    active_argv = argv if argv is not None else sys.argv
    if len(active_argv) <= 1:
        return None

    argv0 = str(active_argv[0])
    if "navig" not in Path(argv0).name.lower() and "navig" not in argv0.lower():
        return None

    return _first_non_global_token(active_argv[1:])


# =============================================================================
# Idempotency cache
# =============================================================================

# Maps app instance → set of already-registered command names.  Uses a
# WeakKeyDictionary so GC'd apps don't leave stale entries that could be
# matched by a new app allocated at the same address.
_registered_app_cmds: WeakKeyDictionary = WeakKeyDictionary()
_registration_lock = threading.Lock()

# Cache of discovered `navig.commands` entry-point commands (name → EntryPoint).
# Populated once per process. Reading entry-point *names* is cheap (metadata only,
# no module import); a plugin's module is imported only when its command is
# actually registered (fast path preserved). This is the CLI analogue of the
# `navig.connectors` / `navig.plugins` entry-point groups — it lets extracted
# plugin packages (e.g. navig-download's `tiktok`) self-register without a hardcoded
# entry in `_EXTERNAL_CMD_MAP`.
_ENTRY_POINT_CMDS: dict[str, object] | None = None


def _clear_registration_cache(target_app: typer.Typer | None = None) -> None:  # type: ignore[name-defined]
    """Clear the registration idempotency cache.

    Pass *target_app* to clear only that app; pass ``None`` to clear all.
    Intended for test isolation — not for production use.
    """
    with _registration_lock:
        if target_app is None:
            _registered_app_cmds.clear()
        else:
            _registered_app_cmds.pop(target_app, None)


# =============================================================================
# Command registration
# =============================================================================


def _register_external_commands(
    *,
    register_all: bool = False,
    target_app: typer.Typer | None = None,  # type: ignore[name-defined]
) -> None:
    """Register external command sub-apps on *target_app*.

    Dispatch strategy:
    - **Known external command** in argv → import only that module (fast path).
    - **Inline command or unknown flag** → skip external imports entirely.
    - **No args / ``--help`` / ``register_all=True``** → import everything.

    Args:
        register_all: Force registration of all external commands (useful for
                      ``--help`` and tooling).
        target_app:   Typer app to register on.  Defaults to ``navig.cli.app``.
    """

    if target_app is None:
        from navig.cli import app
        target_app = app

    app_key = target_app
    with _registration_lock:
        already = _registered_app_cmds.setdefault(app_key, set())

    target = None if register_all else _resolve_cli_target_from_argv()

    # ------------------------------------------------------------------
    # Fast path: target is a known external command → import only it
    # ------------------------------------------------------------------
    if target in _EXTERNAL_CMD_MAP:
        _try_register_one(target_app, target, already)
        return

    # ------------------------------------------------------------------
    # Fast path: target is a plugin-provided command (navig.commands
    # entry-point) → import only it. Checked BEFORE the unknown-command
    # early-return below, else extracted commands would never register.
    # ------------------------------------------------------------------
    if target is not None and target in _entry_point_commands():
        _try_register_ep(target_app, target, already)
        return

    # ------------------------------------------------------------------
    # Flat top-level command: `navig wire <path> [--opts]`
    # (a real command, not an add_typer group, so options after the
    #  positional path parse correctly).
    # ------------------------------------------------------------------
    if target == "wire":
        _try_register_wire(target_app, already)
        return

    # ------------------------------------------------------------------
    # Flat top-level command: `navig apply <block> [--input k=v] [--opts]`
    # ------------------------------------------------------------------
    if target == "apply":
        _try_register_apply(target_app, already)
        return

    # ------------------------------------------------------------------
    # Flat top-level command: `navig undo [op-id] [--list] [--yes] [--json]`
    # (T-068 — options after the optional positional id must parse)
    # ------------------------------------------------------------------
    if target == "undo":
        _try_register_undo(target_app, already)
        return

    # ------------------------------------------------------------------
    # Windows-only AHK command
    # ------------------------------------------------------------------
    if target == "ahk" and sys.platform == "win32":
        _try_register_ahk(target_app, already)
        return

    # ------------------------------------------------------------------
    # Inline command or a flag-like token → no external imports needed
    # ------------------------------------------------------------------
    if target is not None and not target.startswith("-"):
        return

    # ------------------------------------------------------------------
    # Fallback: register everything (bare `navig`, `--help`, etc.)
    # ------------------------------------------------------------------
    for cmd_name in _EXTERNAL_CMD_MAP:
        _try_register_one(target_app, cmd_name, already)

    # Plugin-provided commands (navig.commands entry-points). Runs AFTER the
    # built-in map so a hardcoded name wins the idempotency race during any
    # transition period (the `already` set dedups).
    for cmd_name in _entry_point_commands():
        _try_register_ep(target_app, cmd_name, already)

    _try_register_wire(target_app, already)
    _try_register_apply(target_app, already)
    _try_register_undo(target_app, already)

    if sys.platform == "win32":
        _try_register_ahk(target_app, already)


def _try_register_one(
    target_app: typer.Typer,  # type: ignore[name-defined]
    cmd_name: str,
    already: set[str],
) -> None:
    """Register a single external command if not already registered."""
    if cmd_name in already:
        return
    import importlib

    mod_path, attr = _EXTERNAL_CMD_MAP[cmd_name]
    try:
        mod = importlib.import_module(mod_path)
        target_app.add_typer(
            getattr(mod, attr),
            name=cmd_name,
            hidden=(cmd_name in _HIDDEN_COMMANDS),
        )
        already.add(cmd_name)
    except Exception as exc:
        logger.warning(
            "[navig] command '%s' unavailable (registration failed: %s)",
            cmd_name,
            exc,
        )


def _entry_point_commands() -> dict[str, object]:
    """Discover the `navig.commands` entry-point group (name → EntryPoint).

    Cached per process. Mirrors ``navig.connectors.bootstrap`` discovery — names
    only (no import) so the CLI fast path stays fast.

    Commands whose providing plugin is disabled are DROPPED here (one tiny JSON
    read of ``~/.navig/disabled_commands.json``, regenerated by ``PluginHost``
    on every enable/disable) and stashed in :data:`_DISABLED_EP_CMDS` — they
    fall through to the unknown-command path, where the suggest-and-activate
    flow (`navig.cli.providers`) explains how to re-enable them.
    """
    global _ENTRY_POINT_CMDS
    if _ENTRY_POINT_CMDS is not None:
        return _ENTRY_POINT_CMDS
    out: dict[str, object] = {}
    try:
        from importlib.metadata import entry_points

        try:
            eps = entry_points(group="navig.commands")
        except TypeError:  # Python <3.10 API shape
            eps = entry_points().get("navig.commands", [])  # type: ignore[attr-defined]
        for ep in eps:
            out[ep.name] = ep
    except Exception as exc:  # noqa: BLE001 — discovery must never break the CLI
        logger.debug("[navig] entry-point command discovery skipped: %s", exc)
    if out:
        for cmd, plugin_id in _disabled_command_map().items():
            if cmd in out:
                del out[cmd]
                _DISABLED_EP_CMDS[cmd] = plugin_id
    _ENTRY_POINT_CMDS = out
    return out


# command name → providing (disabled) plugin id; filled by _entry_point_commands()
_DISABLED_EP_CMDS: dict[str, str] = {}


def _disabled_command_map() -> dict[str, str]:
    """Read ~/.navig/disabled_commands.json ({command: plugin_id}); {} if absent."""
    try:
        import json

        from navig.platform.paths import config_dir

        f = config_dir() / "disabled_commands.json"
        if not f.exists():
            return {}
        data = json.loads(f.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001 — never break the CLI over a state file
        logger.debug("[navig] disabled_commands.json unreadable: %s", exc)
        return {}


def _try_register_ep(
    target_app: typer.Typer,  # type: ignore[name-defined]
    cmd_name: str,
    already: set[str],
) -> None:
    """Register a single `navig.commands` entry-point command if not already done.

    ``ep.load()`` returns the referenced object (a ``typer.Typer``), imported only
    now — matching the lazy behaviour of ``_try_register_one``.
    """
    if cmd_name in already:
        return
    ep = _entry_point_commands().get(cmd_name)
    if ep is None:
        return
    try:
        sub = ep.load()  # type: ignore[attr-defined]
        target_app.add_typer(
            sub,
            name=cmd_name,
            hidden=(cmd_name in _HIDDEN_COMMANDS),
        )
        already.add(cmd_name)
    except Exception as exc:
        logger.warning(
            "[navig] entry-point command '%s' unavailable (registration failed: %s)",
            cmd_name,
            exc,
        )


def _try_register_wire(
    target_app: typer.Typer,  # type: ignore[name-defined]
    already: set[str],
) -> None:
    """Register `navig wire` as a flat top-level command (not an add_typer group),
    so `navig wire <path> --dry-run` parses options after the positional path."""
    if "wire" in already:
        return
    try:
        from navig.commands.wire import wire_command

        target_app.command("wire")(wire_command)
        already.add("wire")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[navig] command 'wire' unavailable (registration failed: %s)", exc)


def _try_register_apply(
    target_app: typer.Typer,  # type: ignore[name-defined]
    already: set[str],
) -> None:
    """Register `navig apply <block>` as a flat top-level command, so options
    after the positional block id/spec parse correctly (mirrors `navig wire`)."""
    if "apply" in already:
        return
    try:
        from navig.commands.block import apply_command

        target_app.command("apply")(apply_command)
        already.add("apply")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[navig] command 'apply' unavailable (registration failed: %s)", exc)


def _try_register_undo(
    target_app: typer.Typer,  # type: ignore[name-defined]
    already: set[str],
) -> None:
    """Register `navig undo` as a flat top-level command (not an add_typer group),
    so `navig undo <op-id> --yes` parses options after the optional positional id
    (mirrors `navig wire` / `navig apply`)."""
    if "undo" in already:
        return
    try:
        from navig.commands.undo import undo_command

        target_app.command("undo")(undo_command)
        already.add("undo")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[navig] command 'undo' unavailable (registration failed: %s)", exc)


def _try_register_ahk(
    target_app: typer.Typer,  # type: ignore[name-defined]
    already: set[str],
) -> None:
    """Register the optional Windows AHK sub-app if not already registered."""
    if "ahk" in already:
        return
    try:
        from navig.commands.ahk import ahk_app

        target_app.add_typer(ahk_app, name="ahk")
        already.add("ahk")
    except ImportError:
        pass  # Optional dependency not installed — silently disabled.


# =============================================================================
# Public helpers
# =============================================================================


def get_external_commands() -> list[str]:
    """Return the list of all registered external command names."""
    return list(_EXTERNAL_CMD_MAP.keys())


def is_external_command(name: str) -> bool:
    """Return ``True`` if *name* is a registered external command."""
    return name in _EXTERNAL_CMD_MAP
