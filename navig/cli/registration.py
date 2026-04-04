"""
NAVIG CLI Command Registration

Lazy command registration for external modules. Extracted from __init__.py
to reduce module complexity (P1-14 CLI decomposition).

This module owns:
- _EXTERNAL_CMD_MAP: mapping of command names to (module_path, attr_name)
- _register_external_commands(): registers commands on the Typer app
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import typer

# ============================================================================
# EXTERNAL COMMAND MAP
# ============================================================================
# Commands whose sub-app needs an external module import.
# Format: name → (module_path, attr_name)

_EXTERNAL_CMD_MAP: dict[str, tuple[str, str]] = {
    # ── QUANTUM VELOCITY K4: bridge/farmore/copilot moved here from module-level ──
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
    "tg": ("navig.commands.telegram", "telegram_app"),
    "matrix": ("navig.commands.matrix", "matrix_app"),
    "mx": ("navig.commands.matrix", "matrix_app"),
    "store": ("navig.commands.store", "store_app"),
    "vault": ("navig.commands.vault", "vault_app"),
    "cred": ("navig.commands.vault", "cred_app"),
    # Operating-mode profiles (node / builder / operator / architect)
    "profile": ("navig.commands.profile", "profile_app"),
    # Credential profiles (vault round-trip selection) — was the original "profile" entry
    "cred-profile": ("navig.commands.vault", "profile_app"),
    "flux": ("navig.commands.flux", "flux_app"),
    "fx": ("navig.commands.flux", "flux_app"),
    # ── AI sub-app extraction ─────────────────────────────────────────────────
    "ai": ("navig.commands.ai", "ai_app"),
    # ── Batch extraction: backup, tunnel, skills ──────────────────────────────
    "backup": ("navig.commands.backup", "backup_app"),
    "tunnel": ("navig.commands.tunnel", "tunnel_app"),
    "t": ("navig.commands.tunnel", "tunnel_app"),
    "skills": ("navig.commands.skills", "skills_app"),
    "skill": ("navig.commands.skills", "skills_app"),
    # ── Batch extraction: history, trigger, insights, server-template ─────────
    "history": ("navig.commands.history", "history_app"),
    "hist": ("navig.commands.history", "history_app"),
    "trigger": ("navig.commands.triggers", "trigger_app"),
    "insights": ("navig.commands.insights", "insights_app"),
    "server-template": ("navig.commands.server_template", "server_template_app"),
    # ── Phase 2a: gateway extracted from inline block ─────────────────────────
    "gateway": ("navig.commands.gateway", "gateway_app"),
    "cortex": ("navig.commands.cortex", "cortex_app"),
    "desktop": ("navig.commands.desktop", "desktop_app"),
    "net": ("navig.commands.net", "net_app"),
    "host": ("navig.commands.host", "host_app"),
    "h": ("navig.commands.host", "host_app"),
    "context": ("navig.commands.context", "context_app"),
    "ctx": ("navig.commands.context", "context_app"),
    "index": ("navig.commands.index", "index_app"),
    "flow": ("navig.commands.flow", "flow_app"),
    "scaffold": ("navig.commands.scaffold", "scaffold_app"),
    "app": ("navig.commands.app", "app_app"),
    "a": ("navig.commands.app", "app_app"),
    "file": ("navig.commands.files", "file_app"),
    "f": ("navig.commands.files", "file_app"),
    "log": ("navig.commands.log", "log_app"),
    "logs": ("navig.commands.logs", "app"),
    "l": ("navig.commands.log", "log_app"),
    "config": ("navig.commands.config", "config_app"),
    "stats": ("navig.commands.stats", "stats_app"),
    "health": ("navig.commands.stack", "stack_app"),
    # Top-level compatibility aliases for common ops nouns
    "cert": ("navig.cli.__init__", "web_app"),
    "key": ("navig.commands.host", "host_app"),
    "firewall": ("navig.cli.__init__", "local_app"),
    "dns": ("navig.cli.__init__", "local_app"),
    "port": ("navig.cli.__init__", "local_app"),
    "proxy": ("navig.commands.tunnel", "tunnel_app"),
    "env": ("navig.commands.config", "config_app"),
    "secret": ("navig.commands.vault", "vault_app"),
    "job": ("navig.commands.flow", "flow_app"),
    "wiki": ("navig.commands.wiki", "wiki_app"),
    "alias": ("navig.commands.script", "script_app"),
    "server": ("navig.commands.server", "server_app"),
    "s": ("navig.commands.server", "server_app"),
    "db": ("navig.commands.db", "db_app"),
    "database": ("navig.commands.db", "db_app"),
    # ── Phase 2: Links database ───────────────────────────────────────────
    "links": ("navig.commands.links", "links_app"),
    # ── Phase 3: Knowledge graph ──────────────────────────────────────────
    "kg": ("navig.commands.kg", "kg_app"),
    "knowledge": ("navig.commands.kg", "kg_app"),
    # ── Phase 4: Webhooks ─────────────────────────────────────────────────
    "webhook": ("navig.commands.webhook", "webhook_app"),
    "webhooks": ("navig.commands.webhook", "webhook_app"),
    # ── Phase 3: Go cron daemon CLI ───────────────────────────────────────
    # (navig.commands.cron is the existing gateway-based CLI;
    #  navig.commands.cron_local targets the new Go YAML daemon directly)
    "cron": ("navig.commands.cron", "cron_app"),
    # ── P1-15: Self-diagnostics ───────────────────────────────────────────────
    "doctor": ("navig.commands.doctor", "doctor_app"),
    # ── QUANTUM VELOCITY A: docker lazy dispatch ──────────────────────────────
    # Moved from 175-line inline block → navig/commands/docker.py :: docker_app
    # Saves parsing Typer decorators on every non-docker cold start.
    "docker": ("navig.commands.docker", "docker_app"),
    # ── Prompts: agent system-prompt management (.navig/store/prompts/) ───────
    "prompts": ("navig.commands.prompts", "prompts_app"),
    # ── Browser: Playwright/gateway web automation ────────────────────────────
    # Extracted from inline definition → navig/commands/browser.py :: browser_app
    "browser": ("navig.commands.browser", "browser_app"),
    # ── Universal import engine ───────────────────────────────────────────────
    "import": ("navig.commands.import_cmd", "import_app"),
    # ── Multi-network reliable dispatch (Phase 0/1/2) ─────────────────────────
    "dispatch": ("navig.commands.dispatch", "dispatch_app"),
    "contacts": ("navig.commands.dispatch", "contacts_app"),
    "ct": ("navig.commands.dispatch", "contacts_app"),
    # ── System paths inspection & MCP server registration ──────────────────────
    "paths": ("navig.commands.paths_cmd", "paths_app"),
    "mcp": ("navig.commands.mcp_cmd", "mcp_app"),
    # ── Generic mention & keyword tracker ─────────────────────────────────────
    "radar": ("navig.commands.radar", "radar_app"),
    # ── Unified event observation system ──────────────────────────────────────
    "watch": ("navig.commands.watch_cmd", "watch_app"),
    # ── Flux Mesh: peer management, config sync, remote upgrade ───────────────
    "mesh": ("navig.commands.mesh", "mesh_app"),
    # ── Debug / observability: toggle debug mode, show log sizes ─────────────
    "debug": ("navig.commands.debug_cmd", "debug_app"),
    # ── Spaces context (legacy alias) now routed to unified `space` command ─────
    "spaces": ("navig.commands.space", "space_app"),
    # ── PERF: commands migrated from main.py unconditional try/except blocks ─
    # Were imported on EVERY CLI invocation; now dispatched lazily via this map.
    "telemetry": ("navig.commands.telemetry", "telemetry_app"),
    "wut": ("navig.commands.wut", "app"),
    "eval": ("navig.commands.eval", "app"),
    "agents": ("navig.commands.agents", "app"),
    "webdash": ("navig.commands.webdash", "app"),
    "explain": ("navig.commands.explain", "app"),
    "snapshot": ("navig.commands.snapshot", "app"),
    "replay": ("navig.commands.replay", "app"),
    "cloud": ("navig.commands.cloud", "app"),
    "benchmark": ("navig.commands.benchmark", "app"),
    # ── Finance: beancount double-entry accounting (pip install navig[finance]) ──
    "finance": ("navig.commands.finance", "finance_app"),
    # ── Work: lifecycle/stage tracker for leads, projects, tasks, etc. ────────
    "work": ("navig.commands.work", "work_app"),
    "plans": ("navig.commands.plans", "plans_app"),
    # ── Formerly eager-loaded inline — now lazy via this map ─────────────────
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
    # ── Mount: NTFS junction registry + PowerShell helper generation ──────────
    "mount": ("navig.commands.mount", "mount_app"),
    # ── Phase 5 modularization ────────────────────────────────────────────────
    "update": ("navig.commands.update", "update_app"),
    "proactive": ("navig.commands.proactive", "proactive_app"),
    # ── Packages replacement for Packs ────────────────────────────────────────
    "package": ("navig.commands.package", "package_app"),
    "pack": ("navig.commands.package", "package_app"),
    "packs": ("navig.commands.package", "package_app"),
    # ── P1-14: memory extracted from inline block ─────────────────────────────
    "memory": ("navig.commands.memory", "memory_app"),
}

# Hidden command aliases (short forms and deprecated names)
_HIDDEN_COMMANDS: frozenset[str] = frozenset({
    "tg", "mx", "fx", "h", "a", "f", "l", "s", "database", "hist", "ctx"
})


def _register_external_commands(
    *,
    register_all: bool = False,
    target_app: "typer.Typer | None" = None,
) -> None:
    """Register external command sub-apps.

    Called once from main.py after fast-path check. Uses ``sys.argv``
    to decide *which* commands need importing:

    * If argv[1] is recognised as an inline command (defined in
      cli/__init__.py), **no external modules are imported at all**.
    * If argv[1] is an external command, only *that* module is loaded.
    * If we cannot decide (e.g. ``navig --help`` fell through), we
      import everything so the help screen is complete.

    Args:
        register_all: If True, skip argv heuristic and register every
                      external command. Useful for tests and tooling.
        target_app: Optional Typer app to register commands on.
                    If None, imports from navig.cli (late import).
    """
    import importlib

    # Late import to avoid circular dependency
    if target_app is None:
        from navig.cli import app
        target_app = app

    if register_all:
        target = None  # triggers fallback path below
    else:
        target = sys.argv[1] if len(sys.argv) > 1 else None

    # ------------------------------------------------------------------
    # Fast path: target is a known external command → import only it
    # ------------------------------------------------------------------
    if target in _EXTERNAL_CMD_MAP:
        mod_path, attr = _EXTERNAL_CMD_MAP[target]
        try:
            mod = importlib.import_module(mod_path)
            target_app.add_typer(
                getattr(mod, attr),
                name=target,
                hidden=(target in _HIDDEN_COMMANDS),
            )
        except Exception as _ie:
            sys.stderr.write(
                f"[navig] \u26a0 command '{target}' unavailable (registration failed: {_ie})\n"
            )
        return

    # AHK sub-app (Windows only)
    if target == "ahk" and sys.platform == "win32":
        try:
            from navig.commands.ahk import ahk_app

            target_app.add_typer(ahk_app, name="ahk")
        except ImportError:
            pass  # optional dependency not installed; feature disabled
        return

    # ------------------------------------------------------------------
    # If target is an inline command (or a flag like --debug), skip
    # external imports entirely for maximum startup speed.
    # ------------------------------------------------------------------
    if target is not None and not target.startswith("-"):
        # Likely an inline command – no external imports needed.
        return

    # ------------------------------------------------------------------
    # Fallback: register everything (e.g. bare ``navig`` with no args)
    # ------------------------------------------------------------------
    for cmd_name, (mod_path, attr) in _EXTERNAL_CMD_MAP.items():
        try:
            mod = importlib.import_module(mod_path)
            target_app.add_typer(
                getattr(mod, attr),
                name=cmd_name,
                hidden=(cmd_name in _HIDDEN_COMMANDS),
            )
        except Exception as _ie:
            sys.stderr.write(
                f"[navig] \u26a0 command '{cmd_name}' unavailable (registration failed: {_ie})\n"
            )

    if sys.platform == "win32":
        try:
            from navig.commands.ahk import ahk_app

            target_app.add_typer(ahk_app, name="ahk")
        except ImportError:
            pass  # optional dependency not installed; feature disabled


def get_external_commands() -> list[str]:
    """Return list of external command names."""
    return list(_EXTERNAL_CMD_MAP.keys())


def is_external_command(name: str) -> bool:
    """Check if a command name is an external command."""
    return name in _EXTERNAL_CMD_MAP
