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

import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import typer

# =============================================================================
# External command map — name → (module_path, attr_name)
# =============================================================================

_EXTERNAL_CMD_MAP: dict[str, tuple[str, str]] = {
    "bridge": ("navig.commands.bridge", "bridge_app"),
    "farmore": ("navig.commands.farmore", "farmore_app"),
    "copilot": ("navig.commands.ask", "copilot_app"),
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
    "evolve": ("navig.commands.evolution", "evolution_app"),
    "script": ("navig.commands.script", "script_app"),
    "calendar": ("navig.commands.calendar", "calendar_app"),
    "mode": ("navig.commands.mode", "mode_app"),
    "email": ("navig.commands.email", "email_app"),
    "voice": ("navig.commands.voice", "voice_app"),
    "crash": ("navig.commands.crash", "crash_app"),
    "telegram": ("navig.commands.telegram", "telegram_app"),
    "tg": ("navig.commands.telegram", "telegram_app"),        # hidden alias
    "matrix": ("navig.commands.matrix", "matrix_app"),
    "mx": ("navig.commands.matrix", "matrix_app"),            # hidden alias
    "store": ("navig.commands.store", "store_app"),
    "vault": ("navig.commands.vault", "vault_app"),
    "cred": ("navig.commands.vault", "cred_app"),             # deprecated → navig vault
    "cred-profile": ("navig.commands.vault", "profile_app"),  # deprecated → navig vault profile
    "profile": ("navig.commands.profile", "profile_app"),
    "flux": ("navig.commands.flux", "flux_app"),
    "fx": ("navig.commands.flux", "flux_app"),                # hidden alias
    "ai": ("navig.commands.ai", "ai_app"),
    "brain": ("navig.commands.brain", "brain_app"),
    "backup": ("navig.commands.backup", "backup_app"),
    "tunnel": ("navig.commands.tunnel", "tunnel_app"),
    "t": ("navig.commands.tunnel", "tunnel_app"),             # hidden alias
    "skills": ("navig.commands.skills", "skills_app"),
    "skill": ("navig.commands.skills", "skills_app"),
    "history": ("navig.commands.history", "history_app"),
    "hist": ("navig.commands.history", "history_app"),        # hidden alias
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
    "cost": ("navig.commands.cost", "cost_app"),
    "output-style": ("navig.commands.output_style", "output_style_app"),
    "links": ("navig.commands.links", "links_app"),
    "kg": ("navig.commands.kg", "kg_app"),
    "knowledge": ("navig.commands.kg", "kg_app"),
    "webhook": ("navig.commands.webhook", "webhook_app"),
    "webhooks": ("navig.commands.webhook", "webhook_app"),
    "cron": ("navig.commands.cron", "cron_app"),
    "doctor": ("navig.commands.doctor", "doctor_app"),
    "docker": ("navig.commands.docker", "docker_app"),
    "prompts": ("navig.commands.prompts", "prompts_app"),
    "browser": ("navig.commands.browser", "browser_app"),
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
    "package": ("navig.commands.package", "package_app"),
    "pack": ("navig.commands.package", "package_app"),
    "packs": ("navig.commands.package", "package_app"),
    "memory": ("navig.commands.memory", "memory_app"),
    "connector": ("navig.commands.connector_cmd", "connector_app"),
}

# Commands that should be hidden from `--help` output (short forms, deprecated names).
_HIDDEN_COMMANDS: frozenset[str] = frozenset({
    "tg", "mx", "fx", "h", "a", "f", "l", "s", "t", "q",
    "database", "hist", "ctx", "ct",
})

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

# Maps id(app) → set of already-registered command names.  Prevents duplicate
# add_typer() calls when _register_external_commands() is invoked more than
# once for the same app (e.g. in tests).
_registered_app_cmds: dict[int, set[str]] = {}
_registration_lock = threading.Lock()


def _clear_registration_cache(target_app: typer.Typer | None = None) -> None:  # type: ignore[name-defined]
    """Clear the registration idempotency cache.

    Pass *target_app* to clear only that app; pass ``None`` to clear all.
    Intended for test isolation — not for production use.
    """
    with _registration_lock:
        if target_app is None:
            _registered_app_cmds.clear()
        else:
            _registered_app_cmds.pop(id(target_app), None)


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

    app_key = id(target_app)
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
        sys.stderr.write(
            f"[navig] ⚠ command '{cmd_name}' unavailable"
            f" (registration failed: {exc})\n"
        )


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
