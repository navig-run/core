"""
Slash-command handler mixin for TelegramChannel - Phase 2 extraction.

Provides every ``_handle_*`` command method plus the CLI dispatch table.
Consumed via multiple-inheritance:

    class TelegramChannel(TelegramVoiceMixin, TelegramCommandsMixin, ...): ...

Every method uses ``self`` to call back into ``TelegramChannel``
(``send_message``, ``_api_call``, ``send_typing``, ``_keep_typing``,
``base_url``, ``_bot_token``, ``_user_model_prefs``, ``_debug_users``,
``allowed_users``, ``on_message``, ``_is_debug_mode``,
``_resolve_model_name``).

Public surface (all slash-command handlers):
  _handle_start, _handle_help, _handle_mode
  _handle_models_command, _probe_bridge_grid, _handle_providers,
  _show_provider_model_picker
  _handle_debug, _handle_trace
  _handle_tier_command, _handle_restart
  _handle_audio_menu, _handle_settings_hub, _handle_providers_and_models
  _handle_briefing, _handle_deck, _handle_skill, _skill_list
  _handle_cli_command, _match_cli_command (_SLASH_CLI_MAP class attr)
  _register_commands, _get_deck_url
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import random
import re
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from navig.gateway.channels.types import MessageMetadata
from navig.gateway.channels.utils.decorators import (
    error_handled,
    rate_limited,
    typing_context,
)
from navig.platform.paths import global_config_path, msg_trace_path
from navig.ui.icons import icon as _ni

logger = logging.getLogger(__name__)

# Short timeout for non-critical probes and system commands (bridge ping, uptime, df).
_SHORT_CMD_TIMEOUT: float = 2.0
# Generic 5-second timeout for HTTP fetches and subprocess probes.
_PROBE_TIMEOUT: int = 5

# -- Optional keyboard / session / audio-menu deps ----------------------------
try:
    from navig.gateway.channels.telegram_keyboards import (
        _audio_header_text,
        _settings_hub_text,
        build_audio_keyboard,
        build_settings_hub_keyboard,
    )

    _HAS_KEYBOARDS = True
except ImportError:
    _HAS_KEYBOARDS = False

try:
    from navig.gateway.channels.audio_menu import load_config as _load_audio_config
    from navig.gateway.channels.audio_menu import (
        screen_a_keyboard as _audio_screen_a_kb,
    )
    from navig.gateway.channels.audio_menu import screen_a_text as _audio_screen_a_text

    _HAS_AUDIO_MENU = True
except ImportError:
    _HAS_AUDIO_MENU = False

try:
    from navig.gateway.channels.telegram_sessions import get_session_manager

    _HAS_SESSIONS = True
except ImportError:
    _HAS_SESSIONS = False


def _format_bridge_status(online: bool, url: str) -> str:
    """Return a single HTML line describing Bridge Grid status."""
    _url = html.escape(url)
    if online:
        return f"{_ni('bolt')} <b>Bridge:</b> online at <code>{_url}</code>"
    return f"{_ni('bolt')} <b>Bridge:</b> offline (<code>{_url}</code>)"


def _escape_markdown_v2(text: str) -> str:
    """Escape text for Telegram MarkdownV2 parse mode."""
    return re.sub(r"([_\*\[\]\(\)~`>#+\-=|{}.!\\])", r"\\\1", str(text))


# -- Slash-command registry ----------------------------------------------------
# Single source of truth for every Telegram slash command.
# Drives: CLI dispatch - bot registration (setMyCommands) - /help text.
# To add a CLI-backed command: one entry here.  Done.


@dataclass
class SlashCommandEntry:
    """Metadata for a single Telegram slash command."""

    command: str  # without leading "/"
    description: str  # shown in Telegram command list and /help
    cli_template: str | None = None  # navig CLI template; ``{args}`` is replaced with user input
    handler: str | None = None  # method name on TelegramCommandsMixin to call directly
    visible: bool = True  # include in /help and setMyCommands
    category: str = "general"  # section heading for /help
    usage: str = ""  # optional usage hint shown in /help (e.g. "/cmd [option]")


_SLASH_REGISTRY: list[SlashCommandEntry] = [
    # --- Core ----------------------------------------------------------------
    SlashCommandEntry("start", "Wake up greeting", handler="_handle_start", category="core"),
    SlashCommandEntry(
        "help",
        "Full command reference",
        handler="_handle_help",
        category="core",
    ),
    SlashCommandEntry(
        "helpme",
        "Command reference (alias)",
        handler="_handle_help",
        category="core",
        visible=False,
    ),
    SlashCommandEntry(
        "status",
        "System and spaces status",
        handler="_handle_status",
        category="core",
    ),
    SlashCommandEntry(
        "models",
        "Active model routing table",
        handler="_handle_models_command",
        category="core",
        usage="/models [big|small|coder|auto]",
    ),
    SlashCommandEntry(
        "model",
        "Active model routing table",
        handler="_handle_models_command",
        category="core",
        visible=False,
        usage="/model [big|small|coder|auto]",
    ),
    SlashCommandEntry(
        "routing",
        "Active model routing table",
        handler="_handle_models_command",
        category="core",
        visible=False,
    ),
    SlashCommandEntry(
        "router",
        "Active model routing table",
        handler="_handle_models_command",
        category="core",
        visible=False,
    ),
    SlashCommandEntry("briefing", "Today's summary", handler="_handle_briefing", category="core"),
    SlashCommandEntry(
        "pin",
        "Pin the last bot message (group chats)",
        handler="_handle_pin_cmd",
        category="utilities",
        usage="/pin",
    ),
    SlashCommandEntry(
        "deck",
        "Open the command deck",
        handler="_handle_deck",
        category="core",
        visible=False,
    ),
    SlashCommandEntry("ping", "Quick alive check", handler="_handle_ping", category="core"),
    SlashCommandEntry(
        "skill",
        "Run a NAVIG skill",
        handler="_handle_skill_cmd",
        category="core",
        usage="/skill list  or  /skill <name>",
    ),
    # --- Monitoring ----------------------------------------------------------
    SlashCommandEntry(
        "disk",
        "💽 Disk usage",
        handler="_handle_disk_cmd",
        category="monitoring",
    ),
    SlashCommandEntry(
        "memory",
        "🧠 RAM status",
        handler="_handle_memory_cmd",
        category="monitoring",
    ),
    SlashCommandEntry(
        "cpu",
        "⚡ CPU & load",
        handler="_handle_cpu_cmd",
        category="monitoring",
    ),
    SlashCommandEntry(
        "uptime",
        "🕐 Server uptime",
        handler="_handle_uptime_cmd",
        category="monitoring",
    ),
    SlashCommandEntry(
        "services",
        "⚙️ Running services",
        handler="_handle_services_cmd",
        category="monitoring",
    ),
    SlashCommandEntry(
        "ports",
        "🔌 Open ports",
        handler="_handle_ports_cmd",
        category="monitoring",
    ),
    SlashCommandEntry(
        "top",
        "📊 Process list",
        handler="_handle_cpu_cmd",
        visible=False,
        category="monitoring",
    ),
    SlashCommandEntry(
        "df",
        "💽 Disk usage (df)",
        handler="_handle_disk_cmd",
        visible=False,
        category="monitoring",
    ),
    SlashCommandEntry(
        "cron",
        "Crontab",
        cli_template="run \"crontab -l 2>/dev/null || echo 'no crontab'\"",
        visible=False,
        category="monitoring",
    ),
    # --- Docker --------------------------------------------------------------
    SlashCommandEntry(
        "docker",
        "List containers or view logs",
        handler="_handle_docker_cmd",
        category="docker",
        usage="/docker [ps|logs <name>|restart <name>|all]",
    ),
    SlashCommandEntry(
        "logs",
        "Container logs (+ name)",
        cli_template="docker logs {args} -n 50",
        category="docker",
        usage="/logs <container-name>",
    ),
    SlashCommandEntry(
        "restart",
        "Restart container (+ name) or daemon",
        handler="_handle_restart_cmd",
        category="docker",
        usage="/restart [daemon|<container-name>]",
    ),
    SlashCommandEntry(
        "exec",
        "Execute command in container",
        cli_template="docker exec {args}",
        category="docker",
        usage="/exec <container> <command>",
    ),
    # --- Database ------------------------------------------------------------
    SlashCommandEntry("db", "List databases", cli_template="db list", category="database"),
    SlashCommandEntry(
        "tables",
        "Tables in a database (+ db name)",
        cli_template="db tables {args}",
        category="database",
        usage="/tables <database-name>",
    ),
    SlashCommandEntry(
        "query",
        "Execute SQL query (+ db + query)",
        cli_template='db query "{args}"',
        category="database",
        usage="/query -d <db> <SQL>",
    ),
    # --- Tools ---------------------------------------------------------------
    SlashCommandEntry("hosts", "Configured servers", cli_template="host list", category="tools"),
    SlashCommandEntry(
        "test",
        "Test SSH connectivity to active host",
        cli_template="host test",
        category="tools",
        usage="/test  — verifies SSH connection",
    ),
    SlashCommandEntry(
        "use",
        "Switch active host (+ name)",
        cli_template="host use {args}",
        category="tools",
        usage="/use <hostname>",
    ),
    SlashCommandEntry(
        "apps",
        "List applications on current host",
        cli_template="app list",
        category="tools",
    ),
    SlashCommandEntry(
        "app",
        "Switch active app (+ name)",
        cli_template="app use {args}",
        category="tools",
        usage="/app <app-name>",
    ),
    SlashCommandEntry(
        "files",
        "List remote directory contents",
        cli_template="file list {args}",
        category="tools",
        usage="/files [path]  — defaults to current dir",
    ),
    SlashCommandEntry(
        "cat",
        "View remote file contents",
        cli_template="file show {args}",
        category="tools",
        usage="/cat <path> [--lines N]",
    ),
    SlashCommandEntry(
        "run",
        "Execute remote command",
        cli_template='run "{args}"',
        category="tools",
        usage="/run <shell command>",
    ),
    SlashCommandEntry("backup", "Backup status", cli_template="backup show", category="tools"),
    SlashCommandEntry(
        "tunnels",
        "List active SSH tunnels",
        cli_template="tunnel list",
        category="tools",
        visible=False,
    ),
    SlashCommandEntry(
        "flows",
        "List available workflows",
        cli_template="flow list",
        category="tools",
        visible=False,
    ),
    SlashCommandEntry(
        "vhosts",
        "List web server virtual hosts",
        cli_template="web vhosts",
        category="tools",
        visible=False,
    ),
    SlashCommandEntry(
        "plans", "Plans and spaces progress", cli_template="plans status", category="tools"
    ),
    SlashCommandEntry(
        "plan",
        "Add a plan goal (+ text)",
        cli_template="plans add {args}",
        category="tools",
        usage="/plan <goal text>",
    ),
    SlashCommandEntry(
        "space",
        "Switch active space (+ name)",
        handler="_handle_space",
        category="tools",
        usage="/space <name>",
    ),
    SlashCommandEntry(
        "spaces",
        "List available spaces",
        handler="_handle_spaces",
        category="tools",
        usage="/spaces [name]  — name switches directly",
    ),
    SlashCommandEntry(
        "intake",
        "Guided planning questions (Vision/Roadmap/Phase)",
        handler="_handle_intake",
        category="tools",
    ),
    # --- Formatting & Reasoning --------------------------------------------------
    SlashCommandEntry(
        "format",
        "Convert Markdown to Telegram-friendly format",
        handler="_handle_format",
        category="tools",
        usage="/format <text>",
    ),
    SlashCommandEntry(
        "fmt",
        "Format shorthand (alias for /format)",
        handler="_handle_format",
        category="tools",
        visible=False,
    ),
    SlashCommandEntry(
        "think",
        "Reason through a topic — paginated cards",
        handler="_handle_think",
        category="tools",
        usage="/think <topic or question>",
    ),
    SlashCommandEntry(
        "refine",
        "Sharpen your idea with AI clarification",
        handler="_handle_refine_cmd",
        category="tools",
        usage="/refine <idea or text>",
    ),
    # --- Utilities -----------------------------------------------------------
    SlashCommandEntry(
        "ip",
        "Server public IP",
        cli_template='run "curl -s ifconfig.me"',
        category="utilities",
    ),
    SlashCommandEntry("time", "Server time", cli_template='run "date"', category="utilities"),
    SlashCommandEntry(
        "weather",
        "Weather report (optional city)",
        handler="_handle_weather",
        category="utilities",
        usage="/weather [city]",
    ),
    SlashCommandEntry(
        "dns",
        "DNS lookup (+ domain)",
        cli_template='run "dig +short {args}"',
        category="utilities",
        usage="/dns <domain>",
    ),
    SlashCommandEntry(
        "ssl",
        "SSL cert check (+ domain)",
        cli_template="run \"echo | openssl s_client -connect {args}:443 -servername {args} 2>/dev/null | openssl x509 -noout -dates 2>/dev/null || echo 'no cert found'\"",
        category="utilities",
        usage="/ssl <domain>",
    ),
    SlashCommandEntry(
        "whois",
        "Domain whois (+ domain)",
        cli_template='run "whois {args} | head -30"',
        category="utilities",
        usage="/whois <domain>",
    ),
    SlashCommandEntry(
        "netstat",
        "Network statistics",
        cli_template='run "ss -s"',
        visible=False,
        category="utilities",
    ),
    # --- Model control -------------------------------------------------------
    SlashCommandEntry(
        "ai",
        "Current AI provider + model — switch with one tap",
        handler="_handle_ai_command",
        category="model",
        usage="/ai  — shows provider/model picker with inline keyboard",
    ),
    SlashCommandEntry(
        "ai_model",
        "AI model picker (alias)",
        handler="_handle_ai_command",
        category="model",
        visible=False,
    ),
    SlashCommandEntry(
        "settings",
        "Main config hub - audio, providers, focus, model",
        handler="_handle_settings_hub",
        category="model",
    ),
    SlashCommandEntry(
        "providers",
        "AI Provider Hub",
        handler="_handle_providers",
        category="model",
        usage="/providers [provider-name]",
    ),
    SlashCommandEntry(
        "provider",
        "AI Provider Hub (alias)",
        handler="_handle_providers",
        category="model",
        visible=False,
        usage="/provider [provider-name]",
    ),
    SlashCommandEntry(
        "mode",
        "Set focus mode (work, deep-focus, etc.)",
        handler="_handle_mode",
        category="model",
        usage="/mode [work|deep-focus|coder|auto|list]",
    ),
    SlashCommandEntry(
        "big",
        "Force big model for next message",
        category="model",
        handler="_handle_tier_override",
        usage="/big  (then send your message)",
    ),
    SlashCommandEntry(
        "small",
        "Force small model for next message",
        category="model",
        handler="_handle_tier_override",
        usage="/small  (then send your message)",
    ),
    SlashCommandEntry(
        "coder",
        "Force coder model for next message",
        category="model",
        handler="_handle_tier_override",
        usage="/coder  (then send your message)",
    ),
    SlashCommandEntry(
        "auto",
        "Reset to automatic model selection",
        handler="_handle_tier_override",
        category="model",
        usage="/auto  (then send your message)",
    ),
    # --- Voice & AI settings -------------------------------------------------
    SlashCommandEntry(
        "voice", "Voice & TTS settings", handler="_handle_voice_menu", category="voice"
    ),
    SlashCommandEntry(
        "audio",
        "Voice & TTS settings (alias for /voice)",
        handler="_handle_voice_menu",
        category="voice",
        visible=False,
    ),
    SlashCommandEntry(
        "voicereply",
        "Toggle bot voice replies",
        handler="_handle_audio_menu",
        category="voice",
    ),
    SlashCommandEntry(
        "voiceon",
        "Enable voice input (STT)",
        handler="_handle_voiceon_cmd",
        category="voice",
        usage="/voiceon",
    ),
    SlashCommandEntry(
        "voiceoff",
        "Disable voice input (STT)",
        handler="_handle_voiceoff_cmd",
        category="voice",
        usage="/voiceoff",
    ),
    SlashCommandEntry(
        "provider_voice",
        "Voice provider keys (Deepgram, ElevenLabs)",
        handler="_handle_provider_voice",
        category="voice",
        usage="/provider_voice",
    ),
    SlashCommandEntry(
        "voice_provider",
        "Voice provider keys (alias)",
        handler="_handle_provider_voice",
        category="voice",
        visible=False,
    ),
    # --- Provider control surface --------------------------------------------
    SlashCommandEntry(
        "provider_hybrid",
        "Hybrid routing — assign models to tiers",
        handler="_handle_provider_hybrid",
        category="model",
        usage="/provider_hybrid",
    ),
    SlashCommandEntry(
        "provider_vision",
        "Vision model picker",
        handler="_handle_provider_vision",
        category="model",
        usage="/provider_vision",
    ),
    SlashCommandEntry(
        "provider_show",
        "Show current routing state",
        handler="_handle_provider_show",
        category="model",
        usage="/provider_show",
    ),
    SlashCommandEntry(
        "provider_reset",
        "Reset session provider overrides",
        handler="_handle_provider_reset",
        category="model",
        usage="/provider_reset",
    ),
    SlashCommandEntry(
        "models_reset",
        "Reset tier assignments to provider defaults",
        handler="_handle_models_reset",
        category="model",
        usage="/models_reset",
    ),
    # --- Diagnostics ---------------------------------------------------------
    # --- User / profile ------------------------------------------------------
    SlashCommandEntry(
        "user",
        "Your profile, tier, voice & session info",
        handler="_handle_user",
        category="diagnostics",
    ),
    # --- Diagnostics ---------------------------------------------------------
    SlashCommandEntry(
        "version",
        "Show NAVIG version info",
        handler="_handle_version",
        category="diagnostics",
    ),
    SlashCommandEntry(
        "debug",
        "Package paths, vault, flags",
        handler="_handle_debug",
        category="diagnostics",
    ),
    SlashCommandEntry(
        "trace",
        "Recent conversation history",
        handler="_handle_trace_cmd",
        category="diagnostics",
        usage="/trace  or  /trace debug on|off",
    ),
    SlashCommandEntry(
        "autoheal",
        "Auto-Heal daemon",
        handler="_handle_autoheal",
        category="diagnostics",
        usage="/autoheal [on|off|status|hive on|hive off]",
    ),
    # --- Bot Identity ---
    SlashCommandEntry("about", "Learn about NAVIG", handler="_handle_about", category="core"),
    SlashCommandEntry(
        "auto_start",
        "Enable AI auto-replies",
        handler="_handle_auto_start",
        category="ai",
        usage="/auto_start [persona]",
    ),
    SlashCommandEntry(
        "auto_stop",
        "Disable AI auto-replies",
        handler="_handle_auto_stop",
        category="ai",
    ),
    SlashCommandEntry(
        "auto_status",
        "Check AI conversation status",
        handler="_handle_auto_status",
        category="ai",
    ),
    SlashCommandEntry(
        "continue",
        "Enable autonomous continuation",
        handler="_handle_continue",
        category="ai",
        usage="/continue [conservative|balanced|aggressive] [space]",
    ),
    SlashCommandEntry(
        "pause",
        "Pause autonomous continuation",
        handler="_handle_pause",
        category="ai",
    ),
    SlashCommandEntry(
        "skip",
        "Skip next auto-continuation turn",
        handler="_handle_skip",
        category="ai",
    ),
    SlashCommandEntry(
        "persona",
        "Switch active AI persona",
        handler="_handle_persona",
        category="ai",
        usage="/persona <name>",
    ),
    SlashCommandEntry(
        "personas",
        "List available AI personas",
        handler="_handle_personas",
        category="ai",
    ),
    SlashCommandEntry(
        "explain_ai",
        "AI explains any topic",
        handler="_handle_explain_ai",
        category="ai",
        usage="/explain_ai <topic>",
    ),
    SlashCommandEntry(
        "music",
        "Convert music links (beta)",
        handler="_handle_music",
        category="media",
        visible=False,
    ),
    SlashCommandEntry(
        "imagegen",
        "Generate AI images (beta)",
        handler="_handle_imagegen",
        category="media",
        usage="/imagegen <prompt>",
        visible=False,
    ),
    SlashCommandEntry(
        "profile", "View user profiles", handler="_handle_profile", category="social"
    ),
    SlashCommandEntry(
        "quote",
        "Save and view quotes (beta)",
        handler="_handle_quote",
        category="social",
        visible=False,
    ),
    SlashCommandEntry(
        "respect",
        "Show respect to users (beta)",
        handler="_handle_respect",
        category="social",
        visible=False,
    ),
    SlashCommandEntry(
        "currency",
        "Convert currency (beta)",
        handler="_handle_currency",
        category="utilities",
        usage="/currency <query>",
        visible=False,
    ),
    SlashCommandEntry(
        "crypto_list",
        "List cryptocurrencies (beta)",
        handler="_handle_crypto_list",
        category="utilities",
        visible=False,
    ),
    SlashCommandEntry(
        "remindme",
        "Set reminders",
        handler="_handle_remindme",
        category="utilities",
        usage="/remindme <text> in <time>  or  at <time>",
    ),
    SlashCommandEntry(
        "myreminders",
        "List your active reminders",
        handler="_handle_myreminders",
        category="utilities",
    ),
    SlashCommandEntry(
        "reminders",
        "List your active reminders",
        handler="_handle_myreminders",
        category="utilities",
        visible=False,
    ),
    SlashCommandEntry(
        "cancelreminder",
        "Cancel a reminder",
        handler="_handle_cancelreminder",
        category="utilities",
        usage="/cancelreminder <id>|all",
    ),
    SlashCommandEntry(
        "stats_global",
        "Chat activity statistics (beta)",
        handler="_handle_stats_global",
        category="utilities",
        visible=False,
    ),
    SlashCommandEntry(
        "choice",
        "Make random choices",
        handler="_handle_choice",
        category="utilities",
        usage="/choice <a> or <b> [or <c>...]  — also accepts , or |",
    ),
    SlashCommandEntry(
        "kick",
        "Remove user from chat",
        handler="_handle_kick",
        category="admin",
        usage="/kick <@user|id>",
        visible=False,
    ),
    SlashCommandEntry(
        "mute",
        "Silence user temporarily",
        handler="_handle_mute",
        category="admin",
        usage="/mute <@user|id>",
        visible=False,
    ),
    SlashCommandEntry(
        "unmute",
        "Restore user voice",
        handler="_handle_unmute",
        category="admin",
        usage="/unmute <@user|id>",
        visible=False,
    ),
    SlashCommandEntry(
        "search",
        "Find users",
        handler="_handle_search",
        category="admin",
        usage="/search <query>",
        visible=False,
    ),
    # --- Messaging -----------------------------------------------------------
    SlashCommandEntry(
        "send",
        "Send message via any network",
        handler="_handle_messaging_send",
        category="messaging",
        usage="/send @alias [network] message",
    ),
    SlashCommandEntry(
        "sms",
        "Send SMS shortcut",
        handler="_handle_messaging_sms",
        category="messaging",
        usage="/sms @alias message",
    ),
    SlashCommandEntry(
        "wa",
        "Send WhatsApp shortcut",
        handler="_handle_messaging_wa",
        category="messaging",
        usage="/wa @alias message",
    ),
    SlashCommandEntry(
        "thread",
        "List or show threads",
        handler="_handle_messaging_thread",
        category="messaging",
        usage="/thread [id]",
    ),
    SlashCommandEntry(
        "threads",
        "Active conversations",
        handler="_handle_messaging_threads",
        category="messaging",
        usage="/threads [adapter]",
    ),
    SlashCommandEntry(
        "contact",
        "Show or manage a contact",
        handler="_handle_messaging_contact",
        category="messaging",
        usage="/contact @alias",
    ),
    SlashCommandEntry(
        "contacts",
        "List all contacts",
        handler="_handle_messaging_contacts",
        category="messaging",
    ),
    SlashCommandEntry(
        "reply",
        "Reply to active thread",
        handler="_handle_messaging_reply",
        category="messaging",
        usage="/reply [thread_id] message",
    ),
]


def _iter_unique_registry(*, visible_only: bool = False) -> list[SlashCommandEntry]:
    """Return registry entries deduplicated by command, preserving first occurrence."""
    unique: list[SlashCommandEntry] = []
    seen: set[str] = set()
    for entry in _SLASH_REGISTRY:
        key = entry.command.lower()
        if key in seen:
            continue
        seen.add(key)
        if visible_only and not entry.visible:
            continue
        unique.append(entry)
    return unique


# ---------------------------------------------------------------------------
# Help Encyclopedia — interactive category-based navigation
# ---------------------------------------------------------------------------


@dataclass
class _HelpSubcategory:
    """A subcategory inside a help category (for categories with many commands)."""

    key: str
    label: str
    emoji: str
    commands: list[str]  # command names (without /)


@dataclass
class _HelpCategory:
    """Top-level help category shown as a button on the home screen."""

    key: str
    label: str
    emoji: str
    # Either direct commands OR subcategories — never both.
    commands: list[str] | None = None  # command names from _SLASH_REGISTRY
    subcategories: list[_HelpSubcategory] | None = None


_HELP_CATEGORIES: list[_HelpCategory] = [
    _HelpCategory(
        key="getting_started",
        label="Getting Started",
        emoji="🚀",
        commands=["start", "help", "status", "ping", "about", "version", "skill"],
    ),
    _HelpCategory(
        key="ai_models",
        label="AI & Models",
        emoji="🤖",
        subcategories=[
            _HelpSubcategory(
                "models",
                "Models & Providers",
                "🧠",
                ["ai", "models", "settings", "providers", "mode", "big", "small", "coder", "auto"],
            ),
            _HelpSubcategory(
                "personas", "Personas & Style", "🎭", ["persona", "personas", "explain_ai"]
            ),
            _HelpSubcategory(
                "continuation",
                "Continuation & Auto",
                "⚡",
                [
                    "auto_start",
                    "auto_stop",
                    "auto_status",
                    "continue",
                    "pause",
                    "skip",
                ],
            ),
        ],
    ),
    _HelpCategory(
        key="monitoring",
        label="Monitoring",
        emoji="📊",
        commands=["disk", "memory", "cpu", "uptime", "services", "ports"],
    ),
    _HelpCategory(
        key="docker",
        label="Docker",
        emoji="🐳",
        commands=["docker", "logs", "restart", "exec"],
    ),
    _HelpCategory(
        key="database",
        label="Database",
        emoji="🗄",
        commands=["db", "tables", "query"],
    ),
    _HelpCategory(
        key="operations",
        label="Operations",
        emoji="🛠",
        commands=["hosts", "test", "use", "apps", "app", "files", "cat", "run", "backup"],
    ),
    _HelpCategory(
        key="planning",
        label="Planning & Spaces",
        emoji="🗂",
        commands=["plans", "plan", "space", "spaces", "intake", "briefing"],
    ),
    _HelpCategory(
        key="utilities",
        label="Utilities",
        emoji="🔧",
        subcategories=[
            _HelpSubcategory("network", "Network & DNS", "🌐", ["ip", "dns", "ssl", "whois"]),
            _HelpSubcategory("text", "Text & Reasoning", "📝", ["format", "think", "refine"]),
            _HelpSubcategory(
                "reminders", "Reminders", "⏰", ["remindme", "myreminders", "cancelreminder"]
            ),
            _HelpSubcategory(
                "info",
                "Info & Convert",
                "📌",
                ["time", "weather", "currency", "crypto_list", "choice"],
            ),
        ],
    ),
    _HelpCategory(
        key="voice",
        label="Voice & Audio",
        emoji="🎙",
        commands=["voice", "voicereply", "voiceon", "voiceoff"],
    ),
    _HelpCategory(
        key="diagnostics",
        label="Diagnostics",
        emoji="🔍",
        commands=["user", "debug", "trace", "autoheal"],
    ),
    _HelpCategory(
        key="social",
        label="Social & Media",
        emoji="👥",
        commands=["profile", "quote", "music", "imagegen"],
    ),
]

# Quick lookup: command name → SlashCommandEntry
_HELP_CMD_INDEX: dict[str, SlashCommandEntry] = {}


def _ensure_help_cmd_index() -> dict[str, SlashCommandEntry]:
    """Lazily populate the command lookup index."""
    if not _HELP_CMD_INDEX:
        for entry in _iter_unique_registry(visible_only=False):
            _HELP_CMD_INDEX.setdefault(entry.command.lower(), entry)
    return _HELP_CMD_INDEX


class TelegramCommandsMixin:
    _NL_SWITCH_VERBS: tuple[str, ...] = (
        "switch",
        "focus",
        "work on",
        "use",
        "go to",
        "start",
    )
    _NL_INTAKE_VERBS: tuple[str, ...] = (
        "plan",
        "roadmap",
        "improve",
        "strategy",
        "ask me questions",
        "work for",
    )
    _NL_COMMAND_TRIGGERS: tuple[str, ...] = (
        "show",
        "check",
        "list",
        "get",
        "open",
        "run",
        "restart",
        "switch",
        "use",
        "set",
        "enable",
        "disable",
        "start",
        "stop",
        "cancel",
        "convert",
        "explain",
        "generate",
        "remind",
        "mute",
        "unmute",
        "kick",
        "search",
        "status",
        "help",
    )
    _NL_AMBIGUOUS_COMMANDS: set[str] = {
        "use",
        "run",
        "time",
        "plan",
        "mode",
        "auto",
        "big",
        "small",
        "coder",
        "pause",
        "skip",
        "profile",
    }
    _NL_RISKY_COMMANDS: set[str] = {
        "run",
        "restart",
        "docker",
        "use",
        "space",
        "intake",
        "plan",
        "kick",
        "mute",
        "unmute",
        "search",
    }
    _NL_REQUIRED_ARGS_COMMANDS: set[str] = {
        "logs",
        "tables",
        "use",
        "run",
        "plan",
        "format",
        "think",
        "refine",
        "dns",
        "ssl",
        "whois",
        "currency",
        "choice",
        "kick",
        "mute",
        "unmute",
        "search",
        "persona",
        "explain_ai",
        "imagegen",
        "remindme",
    }
    _NL_DOMAIN_HINTS: dict[str, str] = {
        "money": "finance",
        "budget": "finance",
        "savings": "finance",
        "finance": "finance",
        "health": "health",
        "sleep": "health",
        "fitness": "health",
        "workout": "health",
        "devops": "devops",
        "deploy": "devops",
        "docker": "devops",
        "kubernetes": "devops",
        "infra": "devops",
        "sysops": "sysops",
        "sysadmin": "sysops",
        "uptime": "sysops",
        "patch": "sysops",
        "security": "sysops",
    }

    _INTAKE_QUESTIONS: tuple[tuple[str, str], ...] = (
        ("goal", "What is your main goal for this space in the next 30 days?"),
        ("horizon", "What valuable result should be visible by the end of tomorrow?"),
        ("constraint", "What is your biggest constraint right now (time, money, energy, skills)?"),
        ("assumption", "Which assumption might be wrong and should be challenged first?"),
    )

    _NAV_ROOT_SCREEN = "main"

    """Mixin for TelegramChannel - all slash-command handler methods."""

    def _get_navigation_store(self) -> dict[int, dict[str, Any]]:
        store = getattr(self, "_telegram_nav_state", None)
        if not isinstance(store, dict):
            store = {}
            self._telegram_nav_state = store
        return store

    def _get_navigation_state(self, chat_id: int) -> dict[str, Any]:
        store = self._get_navigation_store()
        state = store.get(chat_id)
        if not isinstance(state, dict):
            state = {
                "chat_id": str(chat_id),
                "screen_stack": [self._NAV_ROOT_SCREEN],
                "data": {},
                "message_id": None,
            }
            store[chat_id] = state
        state.setdefault("chat_id", str(chat_id))
        state.setdefault("screen_stack", [self._NAV_ROOT_SCREEN])
        state.setdefault("data", {})
        state.setdefault("message_id", None)
        if not state["screen_stack"]:
            state["screen_stack"] = [self._NAV_ROOT_SCREEN]
        return state

    def _reset_navigation_state(
        self, chat_id: int, message_id: int | None = None
    ) -> dict[str, Any]:
        store = self._get_navigation_store()
        state = {
            "chat_id": str(chat_id),
            "screen_stack": [self._NAV_ROOT_SCREEN],
            "data": {},
            "message_id": message_id,
        }
        store[chat_id] = state
        return state

    async def renderScreen(
        self,
        chat_id: int,
        screen_name: str,
        payload: dict[str, Any] | None = None,
        message_id: int | None = None,
        user_id: int = 0,
        username: str = "",
    ) -> None:
        state = self._get_navigation_state(chat_id)
        state["data"] = payload or {}
        if message_id is not None:
            state["message_id"] = message_id
        target_message_id = state.get("message_id")

        if screen_name == "main":
            # Main menu has been replaced by the conversational context card.
            # Delegate to /start so all nav:home / nav:cancel callbacks land here.
            await self._handle_start(chat_id=chat_id, username="", user_id=user_id)
            return

        if screen_name == "help":
            text, keyboard = self._build_help_home()
            # Append nav footer for renderScreen context
            keyboard.append(
                [
                    {"text": "🔙 Back", "callback_data": "nav:back"},
                    {"text": "🏠 Home", "callback_data": "nav:home"},
                ]
            )
            if target_message_id:
                if await self.edit_message(
                    chat_id, target_message_id, text, parse_mode="HTML", keyboard=keyboard
                ) is not None:
                    return
            sent = await self.send_message(chat_id, text, parse_mode="HTML", keyboard=keyboard)
            if sent and isinstance(sent, dict):
                state["message_id"] = sent.get("message_id")
            return

        if screen_name == "status":
            await self._handle_status(
                chat_id=chat_id, user_id=user_id, message_id=target_message_id
            )
            return

        if screen_name == "spaces":
            await self._handle_spaces(chat_id=chat_id, message_id=target_message_id)
            return

        if screen_name == "settings":
            await self._handle_settings_hub(
                chat_id=chat_id, user_id=user_id, message_id=target_message_id
            )
            return

        if screen_name == "models":
            await self._handle_models_command(
                chat_id=chat_id, user_id=user_id, message_id=target_message_id
            )
            return

        if screen_name == "providers":
            await self._handle_providers(
                chat_id=chat_id, user_id=user_id, message_id=target_message_id
            )
            return

        if screen_name == "intake":
            await self._handle_intake(
                chat_id=chat_id, user_id=user_id, text="/intake", message_id=target_message_id
            )
            return

        # Fallback recovery screen
        text = f"{_ni('warn')} Something went wrong while opening that screen."
        keyboard = [[{"text": "🏠 Home", "callback_data": "nav:home"}]]
        if target_message_id:
            if await self.edit_message(
                chat_id, target_message_id, text, parse_mode=None, keyboard=keyboard
            ) is not None:
                return
        sent = await self.send_message(chat_id, text, parse_mode=None, keyboard=keyboard)
        if sent and isinstance(sent, dict):
            state["message_id"] = sent.get("message_id")

    async def navigateTo(
        self,
        chat_id: int,
        screen: str,
        *,
        user_id: int = 0,
        username: str = "",
        payload: dict[str, Any] | None = None,
        message_id: int | None = None,
    ) -> None:
        state = self._get_navigation_state(chat_id)
        stack = list(state.get("screen_stack") or [self._NAV_ROOT_SCREEN])
        if not stack or stack[-1] != screen:
            stack.append(screen)
        state["screen_stack"] = stack
        if message_id is not None:
            state["message_id"] = message_id
        await self.renderScreen(
            chat_id=chat_id,
            screen_name=screen,
            payload=payload,
            message_id=state.get("message_id"),
            user_id=user_id,
            username=username,
        )

    async def navigateBack(
        self,
        chat_id: int,
        *,
        user_id: int = 0,
        username: str = "",
        message_id: int | None = None,
    ) -> None:
        state = self._get_navigation_state(chat_id)
        stack = list(state.get("screen_stack") or [self._NAV_ROOT_SCREEN])
        if len(stack) > 1:
            stack.pop()
        prev = stack[-1] if stack else self._NAV_ROOT_SCREEN
        state["screen_stack"] = stack or [self._NAV_ROOT_SCREEN]
        if message_id is not None:
            state["message_id"] = message_id
        await self.renderScreen(
            chat_id=chat_id,
            screen_name=prev,
            message_id=state.get("message_id"),
            user_id=user_id,
            username=username,
        )

    @staticmethod
    def _build_command_list_for_registration() -> list[dict[str, str]]:
        """Build Telegram command registration payload from visible unique entries."""
        return [
            {"command": e.command, "description": e.description}
            for e in _iter_unique_registry(visible_only=True)
        ]

    @staticmethod
    def _generate_help_text(deck_enabled: bool = False) -> str:
        """Generate deterministic HTML /help output."""
        import html as _html

        category_titles = {
            "core": "🚀 Core",
            "ai": f"{_ni('brain')} AI and Models",
            "spaces": "🗂️ Spaces and Planning",
            "plans": "🗂️ Spaces and Planning",
            "tools": "🛠️ Operations",
            "ops": "🛠️ Operations",
            "system": "🛠️ Operations",
        }

        grouped: dict[str, list[SlashCommandEntry]] = {}
        for entry in _iter_unique_registry(visible_only=True):
            if entry.command == "deck" and not deck_enabled:
                continue
            grouped.setdefault(entry.category, []).append(entry)

        lines: list[str] = [
            "📋 <b>NAVIG Command Center</b>",
            "",
            "Use a command below or type naturally.",
            "",
        ]

        for category, commands in grouped.items():
            heading = category_titles.get(category, f"📌 {category.replace('_', ' ').title()}")
            lines.append(f"<b>{heading}</b>")
            for entry in commands:
                cmd = f"/{entry.command}"
                desc = _html.escape((entry.description or "").strip() or "No description", quote=False)
                lines.append(f"• {cmd} — {desc}")
            lines.append("")

        lines.append("💬 Natural language also works: show status, restart daemon, check docker.")
        return "\n".join(lines).rstrip()

    # -- Core slash handlers ---------------------------------------------------

    async def _handle_start(
        self,
        chat_id: int,
        username: str,
        user_id: int = 0,
        prior_last_active: str | None = None,
    ) -> None:
        """Send a conversational context card — no navigation menus."""
        # Active reminder count
        active_count = 0
        try:
            from navig.store.runtime import get_runtime_store

            active_count = len(get_runtime_store().get_user_reminders(user_id) or [])
        except Exception as _rc_exc:  # noqa: BLE001
            logger.debug("reminder count skipped: %s", _rc_exc)  # non-critical for /start display

        # Current model tier preference
        tier_raw = (getattr(self, "_user_model_prefs", {}) or {}).get(user_id, "")
        tier = str(tier_raw).capitalize() if tier_raw else "Auto"

        reminder_line = (
            f"{_ni('reminder')} {active_count} active reminder{'s' if active_count != 1 else ''}"
            if active_count
            else f"{_ni('reminder')} No active reminders"
        )
        text = "\n".join(
            [
                f"{_ni('robot')} <b>NAVIG is ready</b>",
                "",
                reminder_line,
                f"{_ni('brain')} Model: <code>{tier}</code>",
                "",
                "Type naturally or use a command:",
                "<code>/remindme</code> · <code>/myreminders</code> · <code>/status</code> · <code>/briefing</code>",
                "",
                "Need more? → /helpme",
            ]
        )
        keyboard = [[{"text": "📋 What can I do?", "callback_data": "helpme"}]]
        await self.send_message(chat_id, text, parse_mode="HTML", keyboard=keyboard)

        # Away summary — show a brief context recap when the user returns after
        # a long absence.  The feature is gated by memory.away_summary_gap_hours
        # (0 = disabled) and applies only to DM sessions with prior history.
        try:
            from navig.config import get_config_manager as _gcm_start
            from navig.gateway.channels.away_summary import build_away_summary
            from navig.gateway.channels.telegram_sessions import (
                get_session_manager as _getsm_start,
            )

            _cfg_start = _gcm_start()
            _gap_hours = float(_cfg_start.get("memory.away_summary_gap_hours", 4) or 4)
            if _gap_hours > 0:
                _sm_start = _getsm_start()
                _sess_start = _sm_start.get_session(chat_id, user_id, is_group=False)
                if _sess_start and _sess_start.messages:
                    _last_active_raw = prior_last_active or _sess_start.last_active
                    _last_active = datetime.fromisoformat(_last_active_raw)
                    _gap_secs = (
                        datetime.now() - _last_active.replace(tzinfo=None)
                    ).total_seconds()
                    if _gap_secs >= _gap_hours * 3600:
                        _window = int(
                            _cfg_start.get("memory.away_summary_message_window", 30) or 30
                        )
                        _history = [
                            {"role": m.role, "content": m.content}
                            for m in _sess_start.messages[-_window:]
                        ]
                        _summary = await build_away_summary(_history, config=_cfg_start)
                        if _summary:
                            await self.send_message(
                                chat_id,
                                f"💭 <b>Last time:</b> {html.escape(_summary)}",
                                parse_mode="HTML",
                            )
        except Exception as _away_exc:
            logger.debug("away_summary skipped (non-fatal): %s", _away_exc)  # /start must not block

        # Onboarding handoff progress block (text only, no navigation buttons)
        try:
            from navig.commands.init import (
                consume_chat_onboarding_handoff_state,
                get_chat_onboarding_step_progress,
            )

            handoff = consume_chat_onboarding_handoff_state()
            steps = get_chat_onboarding_step_progress()
            if not handoff:
                home_navig_dir = Path.home() / ".navig"
                handoff = consume_chat_onboarding_handoff_state(home_navig_dir)
                if handoff:
                    steps = get_chat_onboarding_step_progress(home_navig_dir)
        except Exception:
            handoff = None
            steps = []

        if not handoff:
            return

        profile = str(handoff.get("profile") or "quickstart")
        if not steps:
            steps = handoff.get("steps") or []
        pending_steps = [step for step in steps if not step.get("completed")]
        completed_count = len(steps) - len(pending_steps)
        checklist_lines = []
        for step in steps:
            mark = "✅" if step.get("completed") else "⬜"
            checklist_lines.append(f"{mark} {step.get('label', '')}")

        onboarding_text = "\n".join(
            [
                "✨ <b>Welcome to NAVIG setup</b>",
                f"Profile: <code>{profile}</code>",
                "",
                f"Onboarding progress: <code>{completed_count}/{len(steps)}</code>",
                *checklist_lines,
                "",
                "Next steps:",
                *(f"• {step.get('hint', '')}" for step in pending_steps[:2]),
                "• Start intake: `/intake`",
                "• Check status: `/status`",
            ]
        )
        await self.send_message(chat_id, onboarding_text, parse_mode="HTML")

    # -- Help Encyclopedia (interactive, in-place editing) -------------------

    @staticmethod
    def _build_help_home() -> tuple[str, list[list[dict[str, str]]]]:
        """Build the Help Encyclopedia home screen (text + keyboard).

        Returns (text, keyboard) for send_message / edit_message.
        """
        lines = [
            "📋  <b>NAVIG Command Center</b>",
            "",
            "Tap a category to explore commands.",
            "Type naturally any time — no commands needed.",
        ]
        text = "\n".join(lines)

        # 2 buttons per row
        rows: list[list[dict[str, str]]] = []
        row: list[dict[str, str]] = []
        for cat in _HELP_CATEGORIES:
            row.append(
                {
                    "text": f"{cat.emoji} {cat.label}",
                    "callback_data": f"help:c:{cat.key}",
                }
            )
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        # Close button
        rows.append([{"text": "✕ Close", "callback_data": "help:close"}])
        return text, rows

    @staticmethod
    def _build_help_category(cat_key: str) -> tuple[str, list[list[dict[str, str]]]] | None:
        """Build a category detail or subcategory-chooser screen.

        Returns ``None`` when *cat_key* is not found.
        """
        cat = next((c for c in _HELP_CATEGORIES if c.key == cat_key), None)
        if cat is None:
            return None

        idx = _ensure_help_cmd_index()

        # -- Category with subcategories → show sub-buttons ----------------
        if cat.subcategories:
            lines = [
                f"{cat.emoji}  <b>{cat.label}</b>",
                "",
                "Choose a section:",
            ]
            rows: list[list[dict[str, str]]] = []
            for sub in cat.subcategories:
                rows.append(
                    [
                        {
                            "text": f"{sub.emoji} {sub.label}",
                            "callback_data": f"help:s:{cat_key}:{sub.key}",
                        }
                    ]
                )
            rows.append(
                [
                    {"text": "◀ Categories", "callback_data": "help:home"},
                    {"text": "✕ Close", "callback_data": "help:close"},
                ]
            )
            return "\n".join(lines), rows

        # -- Category with direct commands ---------------------------------
        cmds = cat.commands or []
        lines = [
            f"{cat.emoji}  <b>{cat.label}</b>",
            "",
        ]
        for cmd_name in cmds:
            entry = idx.get(cmd_name)
            if entry:
                desc = entry.description or "No description"
                lines.append(f"• /{cmd_name} — {desc}")
            else:
                lines.append(f"• /{cmd_name}")
        text = "\n".join(lines)

        rows = []
        # Command detail buttons — 2 per row
        row: list[dict[str, str]] = []
        for cmd_name in cmds:
            if idx.get(cmd_name):
                row.append(
                    {
                        "text": f"/{cmd_name}",
                        "callback_data": f"help:d:{cmd_name}:{cat_key}",
                    }
                )
                if len(row) == 2:
                    rows.append(row)
                    row = []
        if row:
            rows.append(row)
        rows.append(
            [
                {"text": "◀ Categories", "callback_data": "help:home"},
                {"text": "✕ Close", "callback_data": "help:close"},
            ]
        )
        return text, rows

    @staticmethod
    def _build_help_subcategory(
        cat_key: str, sub_key: str
    ) -> tuple[str, list[list[dict[str, str]]]] | None:
        """Build a subcategory command-list screen."""
        cat = next((c for c in _HELP_CATEGORIES if c.key == cat_key), None)
        if cat is None or not cat.subcategories:
            return None
        sub = next((s for s in cat.subcategories if s.key == sub_key), None)
        if sub is None:
            return None

        idx = _ensure_help_cmd_index()
        # HTML: no escape needed — use element tags instead

        lines = [
            f"{sub.emoji}  <b>{sub.label}</b>",
            f"<i>{cat.emoji} {cat.label}</i>",
            "",
        ]
        for cmd_name in sub.commands:
            entry = idx.get(cmd_name)
            if entry:
                import html as _html
                desc = _html.escape(entry.description or "No description", quote=False)
                lines.append(f"• /{cmd_name} — {desc}")
            else:
                lines.append(f"• /{cmd_name}")
        text = "\n".join(lines)

        rows: list[list[dict[str, str]]] = []
        row: list[dict[str, str]] = []
        for cmd_name in sub.commands:
            if idx.get(cmd_name):
                row.append(
                    {
                        "text": f"/{cmd_name}",
                        "callback_data": f"help:d:{cmd_name}:{cat_key}:{sub_key}",
                    }
                )
                if len(row) == 2:
                    rows.append(row)
                    row = []
        if row:
            rows.append(row)
        rows.append(
            [
                {"text": f"◀ {cat.emoji} {cat.label}", "callback_data": f"help:c:{cat_key}"},
                {"text": "✕ Close", "callback_data": "help:close"},
            ]
        )
        return text, rows

    @staticmethod
    def _build_help_command_detail(
        cmd_name: str,
        back_cat: str,
        back_sub: str | None = None,
    ) -> tuple[str, list[list[dict[str, str]]]] | None:
        """Build a single-command detail screen."""
        idx = _ensure_help_cmd_index()
        entry = idx.get(cmd_name)
        if entry is None:
            return None

        import html as _html
        desc = _html.escape(entry.description or "No description", quote=False)
        lines = [
            f"<b>/{entry.command}</b>",
            "",
            f"📄 {desc}",
        ]
        if entry.usage:
            lines.append(f"\n💡 <b>Usage:</b>  <code>{entry.usage}</code>")
        if entry.cli_template:
            lines.append(f"🔗 <b>CLI:</b>  <code>{entry.cli_template}</code>")
        lines.append(f"\n📁 <b>Category:</b>  {entry.category}")

        text = "\n".join(lines)

        # Back button
        if back_sub:
            back_data = f"help:s:{back_cat}:{back_sub}"
        else:
            back_data = f"help:c:{back_cat}"
        keyboard = [
            [
                {"text": "◀ Back", "callback_data": back_data},
                {"text": "🏠 Categories", "callback_data": "help:home"},
                {"text": "✕ Close", "callback_data": "help:close"},
            ],
        ]
        return text, keyboard

    async def _handle_help_callback(self, cb_data: str, chat_id: int, message_id: int) -> None:
        """Route a ``help:*`` callback to the correct builder and edit in-place."""
        parts = cb_data.split(":")

        # help:close → delete the message
        if len(parts) >= 2 and parts[1] == "close":
            try:
                await self._api_call(
                    "deleteMessage",
                    {"chat_id": chat_id, "message_id": message_id},
                )
            except Exception:
                logger.debug("help: deleteMessage failed for %s/%s", chat_id, message_id)
            return

        result: tuple[str, list] | None = None

        if len(parts) >= 2 and parts[1] == "home":
            result = TelegramCommandsMixin._build_help_home()

        elif len(parts) >= 3 and parts[1] == "c":
            result = TelegramCommandsMixin._build_help_category(parts[2])

        elif len(parts) >= 4 and parts[1] == "s":
            result = TelegramCommandsMixin._build_help_subcategory(parts[2], parts[3])

        elif len(parts) >= 4 and parts[1] == "d":
            cmd_name = parts[2]
            back_cat = parts[3]
            back_sub = parts[4] if len(parts) >= 5 else None
            result = TelegramCommandsMixin._build_help_command_detail(cmd_name, back_cat, back_sub)

        if result is None:
            # Fallback — return to home
            result = TelegramCommandsMixin._build_help_home()

        text, keyboard = result
        await self.edit_message(chat_id, message_id, text, parse_mode="HTML", keyboard=keyboard)

    async def _handle_help(
        self,
        chat_id: int,
        message_id: int | None = None,
        topic: str | None = None,
    ) -> None:
        """Interactive help encyclopedia (/help [topic], /helpme).

        When *topic* matches a ``_HELP_CATEGORIES`` key (e.g. ``/help docker``),
        opens that category screen directly instead of the home screen.
        Subsequent navigation happens via ``help:*`` callbacks that edit the
        message in-place.
        """
        result = None
        if topic:
            result = TelegramCommandsMixin._build_help_category(topic.lower())
            if result is None:
                # Unknown topic — try matching by label (case-insensitive)
                result = next(
                    (
                        TelegramCommandsMixin._build_help_category(cat.key)
                        for cat in _HELP_CATEGORIES
                        if cat.label.lower() == topic.lower()
                    ),
                    None,
                )
        text, keyboard = result if result is not None else TelegramCommandsMixin._build_help_home()
        sent = await self.send_message(chat_id, text, parse_mode="HTML", keyboard=keyboard)
        # Track the message so future nav: callbacks can edit it
        if sent and isinstance(sent, dict):
            state = self._get_navigation_state(chat_id)
            state["message_id"] = sent.get("message_id")

    async def _handle_ping(self, chat_id: int, user_id: int = 0) -> None:
        """Live heartbeat card — version, host, space, tier, reminders, bridge (/ping)."""
        import asyncio as _asyncio

        lines = ["🏓 <b>pong</b> — NAVIG is live", ""]

        # Version
        try:
            import navig as _navig_pkg

            ver = getattr(_navig_pkg, "__version__", "unknown")
        except Exception:
            ver = "unknown"
        lines.append(f"Version: <code>{ver}</code>")

        # Active host
        try:
            from navig.config import load_config

            cfg = load_config()
            active_host = (cfg.get("active_host") or "—") if cfg else "—"
        except Exception:
            active_host = "—"
        lines.append(f"Host: <code>{active_host}</code>")

        # Active space
        try:
            from navig.commands.space import get_active_space

            space = get_active_space() or "—"
        except Exception:
            space = "—"
        lines.append(f"Space: <code>{space}</code>")

        # Model tier
        tier_raw = (getattr(self, "_user_model_prefs", {}) or {}).get(user_id, "")
        tier = str(tier_raw).capitalize() if tier_raw else "Auto"
        lines.append(f"Model: <code>{tier}</code>")

        # Active reminders
        try:
            from navig.store.runtime import get_runtime_store

            active_count = len(get_runtime_store().get_user_reminders(user_id) or [])
            lines.append(f"Reminders: <code>{active_count} active</code>")
        except Exception as _rc2_exc:  # noqa: BLE001
            logger.debug("reminder count for /status skipped: %s", _rc2_exc)

        # Bridge status (non-blocking, 2 s timeout)
        try:
            bridge_ok, bridge_url = await _asyncio.wait_for(self._probe_bridge_grid(), timeout=_SHORT_CMD_TIMEOUT)
            bridge_status = f"🟢 {bridge_url}" if bridge_ok else "🔴 offline"
        except Exception:
            bridge_status = "❔ unknown"
        lines.append(f"Bridge: {bridge_status}")

        await self.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    async def _handle_status(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
    ) -> None:
        """System status summary for Telegram users (/status)."""
        from navig.spaces import get_default_space
        from navig.spaces.progress import (
            collect_spaces_progress,
            format_spaces_progress_lines,
        )

        selected_space = get_default_space()
        rows = collect_spaces_progress()

        # ── Active host ──────────────────────────────────────────────────────
        # Use the canonical config-manager resolution chain (env → project →
        # cache → global).  When nothing is configured the user is operating
        # locally, so fall back explicitly to "localhost".
        try:
            from navig.config import get_config_manager

            active_host: str = get_config_manager().get_active_host() or "localhost"
        except Exception:
            active_host = "localhost"

        # ── Model tier ───────────────────────────────────────────────────────
        if hasattr(self, "_get_user_tier_pref"):
            tier = self._get_user_tier_pref(chat_id, user_id)
        else:
            tier = (getattr(self, "_user_model_prefs", {}) or {}).get(user_id, "")
        model_label = tier or "auto"

        # ── Active persona ───────────────────────────────────────────────────
        try:
            from navig.personas.store import get_active_persona

            persona = get_active_persona(user_id, chat_id) or "default"
        except Exception:
            persona = "default"

        # ── Active reminders ─────────────────────────────────────────────────
        reminder_count: int | None = None
        try:
            from navig.store.runtime import get_runtime_store

            reminder_count = len(get_runtime_store().get_user_reminders(user_id) or [])
        except Exception:  # noqa: BLE001
            pass  # best-effort; runtime store may not be available

        # ── Setup readiness ────────────────────────────────────────────────────
        readiness_state = "unknown"
        readiness_score = 0
        status_fix_issues: list[dict[str, str]] = []
        try:
            from navig.commands.init import get_init_status_payload

            init_status = get_init_status_payload()
            readiness = init_status.get("readiness", {}) if isinstance(init_status, dict) else {}
            readiness_state = str(readiness.get("state") or "unknown")
            readiness_score = int(readiness.get("score") or 0)
            issues = readiness.get("issues", []) if isinstance(readiness, dict) else []
            if isinstance(issues, list):
                status_fix_issues = [i for i in issues if isinstance(i, dict)]
        except Exception:  # noqa: BLE001
            pass  # best-effort; init status may not be available

        # ── Assemble message ────────────────────────────────────────────────────
        readiness_icon = "✅" if readiness_state == "ready" else "⚠️"
        reminder_str = f"<code>{reminder_count} active</code>" if reminder_count is not None else "<code>—</code>"

        lines: list[str] = [
            "🧭 <b>NAVIG Status</b>",
            "",
            "🖥  <b>Infrastructure</b>",
            f"Host      <code>{active_host}</code>",
            f"Space     <code>{selected_space}</code>",
            "",
            "🤖  <b>AI Session</b>",
            f"Model     <code>{model_label}</code>",
            f"Persona   <code>{persona}</code>",
            "",
            f"⏰  <b>Reminders</b>   {reminder_str}",
            "",
            f"{readiness_icon}  <b>Setup</b>   <code>{readiness_state}</code> ({readiness_score}%)",
        ]

        # Setup fix hints (max 2)
        if status_fix_issues:
            lines.append("")
            lines.append("<b>Pending fixes:</b>")
            for issue in status_fix_issues[:2]:
                if not isinstance(issue, dict):
                    continue
                summary = str(issue.get("summary") or "").strip()
                command = str(issue.get("command") or "").strip()
                if summary and command:
                    lines.append(f"  • {summary} → <code>{command}</code>")
            remaining = len(status_fix_issues) - 2
            if remaining > 0:
                lines.append(f"  • +{remaining} more — run <code>navig init --status</code>")

        # Progression
        lines.append("")
        lines.append("📈  <b>Progression</b>")
        if rows:
            lines.extend(format_spaces_progress_lines(rows, max_items=5))
        else:
            lines.append("   <i>No spaces discovered yet.</i>")

        # ── Build fix buttons (shared between both send paths) ────────────────
        fix_buttons: list[list[dict[str, str]]] = []
        for issue in status_fix_issues[:2]:
            code = str(issue.get("code") or "").strip()
            summary = str(issue.get("summary") or "").strip()
            if not code:
                continue
            title = summary or "Run setup fix"
            if len(title) > 36:
                title = title[:33] + "..."
            fix_buttons.append([{"text": f"🛠 {title}", "callback_data": f"stfix:{code}"}])

        text = "\n".join(lines)

        # ── Deliver ───────────────────────────────────────────────────────────
        if message_id:
            keyboard: list[list[dict[str, str]]] = fix_buttons + [
                [
                    {"text": "🔙 Back", "callback_data": "nav:back"},
                    {"text": "🏠 Home", "callback_data": "nav:home"},
                ],
            ]
            await self.edit_message(
                chat_id,
                message_id,
                text,
                parse_mode="HTML",
                keyboard=keyboard,
            )
            return

        await self.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            keyboard=fix_buttons or None,
        )

    async def _handle_status_fix_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        user_id: int,
    ) -> None:
        """Execute one readiness recovery command selected from Telegram /status."""
        issue_code = cb_data.split(":", 1)[1].strip() if ":" in cb_data else ""
        if not issue_code:
            await self._api_call(
                "answerCallbackQuery",
                {
                    "callback_query_id": cb_id,
                    "text": "⚠️ Invalid setup action",
                    "show_alert": False,
                },
            )
            return

        try:
            from navig.commands.init import get_init_status_payload

            payload = get_init_status_payload()
            readiness = payload.get("readiness", {}) if isinstance(payload, dict) else {}
            issues = readiness.get("issues", []) if isinstance(readiness, dict) else []
        except Exception:
            issues = []

        selected = None
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            if str(issue.get("code") or "").strip() == issue_code:
                selected = issue
                break

        if not selected:
            await self._api_call(
                "answerCallbackQuery",
                {
                    "callback_query_id": cb_id,
                    "text": "✅ Already resolved",
                    "show_alert": False,
                },
            )
            return

        command = str(selected.get("command") or "").strip()
        if not command:
            await self._api_call(
                "answerCallbackQuery",
                {
                    "callback_query_id": cb_id,
                    "text": "⚠️ No command available",
                    "show_alert": False,
                },
            )
            return

        navig_cmd = command
        if navig_cmd.lower().startswith("navig "):
            navig_cmd = navig_cmd[6:]

        in_flight = getattr(self, "_status_fix_inflight", None)
        if not isinstance(in_flight, set):
            in_flight = set()
            self._status_fix_inflight = in_flight

        inflight_key = (int(chat_id), int(user_id), issue_code)
        if inflight_key in in_flight:
            await self._api_call(
                "answerCallbackQuery",
                {
                    "callback_query_id": cb_id,
                    "text": "⏳ Setup fix already running",
                    "show_alert": False,
                },
            )
            return

        in_flight.add(inflight_key)

        await self._api_call(
            "answerCallbackQuery",
            {
                "callback_query_id": cb_id,
                "text": "🚀 Running setup fix",
                "show_alert": False,
            },
        )
        try:
            await self._handle_cli_command(chat_id, user_id, {}, navig_cmd)
        finally:
            in_flight.discard(inflight_key)
            await self.send_message(
                chat_id,
                "✅ Setup fix command finished. Refresh status to verify readiness.",
                parse_mode=None,
                keyboard=[
                    [{"text": "🔄 Refresh status", "callback_data": "nav:open:status"}],
                ],
            )

    def _runtime_state_with_context(
        self,
        user_id: int,
        chat_id: int,
        context: dict[str, Any],
    ) -> None:
        from navig.store.runtime import get_runtime_store

        store = get_runtime_store()
        state = store.get_ai_state(user_id) or {}
        store.set_ai_state(
            user_id=user_id,
            chat_id=chat_id,
            mode=state.get("mode") or "active",
            persona=state.get("persona") or "assistant",
            context=context,
        )

    @staticmethod
    def _mark_chat_onboarding_step(step_id: str) -> None:
        try:
            from navig.commands.init import mark_chat_onboarding_step_completed

            mark_chat_onboarding_step_completed(step_id)
        except (ImportError, AttributeError, TypeError, ValueError):
            logger.debug("Failed to mark chat onboarding step: %s", step_id)

    @staticmethod
    def _is_cli_command_success(response: str) -> bool:
        txt = str(response or "")
        if not txt.strip():
            return False
        if "Command exited with code:" not in txt:
            return True
        import re

        match = re.search(r"Command exited with code:\s*(\d+)", txt)
        if not match:
            return False
        return int(match.group(1)) == 0

    @staticmethod
    def _has_host_connectivity_confirmation(response: str) -> bool:
        import re

        txt = str(response or "")
        if not txt.strip():
            return False

        patterns = (
            r"\bconnect(?:ed|ivity)?\b",
            r"\breachable\b",
            r"\bssh\s+(?:ok|success|connected)\b",
            r"\bhost\s+test\s+(?:passed|ok|successful)\b",
            r"\bconnectivity\s+(?:verified|confirmed|ok|successful)\b",
        )
        return any(re.search(pattern, txt, flags=re.IGNORECASE) for pattern in patterns)

    def _bootstrap_space_docs(self, space: str, space_path: Path) -> None:
        space_path.mkdir(parents=True, exist_ok=True)

        vision = space_path / "VISION.md"
        if not vision.exists():
            vision.write_text(
                f"---\ngoal: {space} goals\n---\n\n# {space.title()} Vision\n\n",
                encoding="utf-8",
            )

        roadmap = space_path / "ROADMAP.md"
        if not roadmap.exists():
            roadmap.write_text("# Roadmap\n\n", encoding="utf-8")

        current_phase = space_path / "CURRENT_PHASE.md"
        if not current_phase.exists():
            current_phase.write_text(
                "---\ncompletion_pct: 0\n---\n\n# Current Phase\n\n",
                encoding="utf-8",
            )

    async def _handle_spaces(
        self, chat_id: int, user_id: int = 0, text: str = "", message_id: int | None = None
    ) -> None:
        # Quick-switch: "/spaces devops" delegates directly to /space
        arg = text[len("/spaces") :].strip() if text.lower().startswith("/spaces") else ""
        if arg:
            await self._handle_space(chat_id=chat_id, user_id=user_id, text=f"/space {arg}")
            return

        from navig.commands.space import get_active_space
        from navig.spaces.contracts import CANONICAL_SPACES

        active = get_active_space()
        lines = ["<b>Spaces</b>", f"Active: <code>{html.escape(active)}</code>", "", "Available:"]
        for name in CANONICAL_SPACES:
            marker = "•"
            if name == active:
                marker = "▸"
            lines.append(f"{marker} <code>{html.escape(name)}</code>")
        lines.append("\nUse <code>/space &lt;name&gt;</code> or choose below.")
        keyboard = [[{"text": "🧭 Start Intake", "callback_data": "nav:open:intake"}]]
        if message_id:
            keyboard.insert(
                0,
                [
                    {"text": "🔙 Back", "callback_data": "nav:back"},
                    {"text": "🏠 Home", "callback_data": "nav:home"},
                ],
            )
        if message_id:
            await self.edit_message(
                chat_id,
                message_id,
                "\n".join(lines),
                parse_mode="HTML",
                keyboard=keyboard,
            )
            return
        await self.send_message(chat_id, "\n".join(lines), parse_mode="HTML", keyboard=keyboard)

    async def _handle_space(self, chat_id: int, user_id: int, text: str = "") -> None:
        from navig.commands.space import _set_active_space, _spaces_dir
        from navig.spaces.contracts import normalize_space_name, validate_space_name
        from navig.spaces.kickoff import build_space_kickoff

        raw = (text or "").strip()
        arg = raw[len("/space") :].strip() if raw.lower().startswith("/space") else ""
        if not arg:
            await self._handle_spaces(chat_id)
            return

        if not validate_space_name(arg):
            await self.send_message(
                chat_id,
                "Unknown space. Use <code>/spaces</code> to see valid names.",
                parse_mode="HTML",
            )
            return

        selected = normalize_space_name(arg)
        space_path = _spaces_dir() / selected
        self._bootstrap_space_docs(selected, space_path)
        _set_active_space(selected)

        kickoff = build_space_kickoff(selected, space_path, cwd=Path.cwd(), max_items=3)
        lines = [f"✅ Active space: <code>{selected}</code>", f"Goal: {html.escape(kickoff.goal)}"]
        if kickoff.actions:
            lines.append("Top next actions:")
            for index, action in enumerate(kickoff.actions, start=1):
                lines.append(f"{index}. {html.escape(action)}")
        else:
            lines.append("No next actions found yet.")
            lines.append("Run <code>/intake</code> to build Vision/Roadmap/Current Phase quickly.")

        from navig.store.runtime import get_runtime_store

        store = get_runtime_store()
        state = store.get_ai_state(user_id) or {}
        context = dict(state.get("context") or {})
        continuation = dict(context.get("continuation") or {})
        continuation["space"] = selected
        context["continuation"] = continuation
        self._runtime_state_with_context(user_id, chat_id, context)

        await self.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    def _append_markdown_section(self, path: Path, heading: str, lines: list[str]) -> None:
        existing = ""
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing and not existing.endswith("\n"):
                existing += "\n"
        content = existing + f"\n## {heading}\n\n" + "\n".join(lines) + "\n"
        _tmp_path: Path | None = None
        try:
            _fd, _tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            _tmp_path = Path(_tmp)
            with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                _fh.write(content)
            os.replace(_tmp_path, path)
            _tmp_path = None
        finally:
            if _tmp_path is not None:
                _tmp_path.unlink(missing_ok=True)

    def _apply_intake_to_space_docs(self, space: str, answers: dict[str, str]) -> Path:
        from navig.commands.space import _spaces_dir

        space_path = _spaces_dir() / space
        self._bootstrap_space_docs(space, space_path)
        date_label = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        vision = space_path / "VISION.md"
        self._append_markdown_section(
            vision,
            f"Intake {date_label}",
            [
                f"- Goal (30d): {answers.get('goal', '')}",
                f"- Biggest constraint: {answers.get('constraint', '')}",
                f"- Assumption to challenge: {answers.get('assumption', '')}",
            ],
        )

        roadmap = space_path / "ROADMAP.md"
        self._append_markdown_section(
            roadmap,
            f"Intake {date_label}",
            [f"- Outcome target (tomorrow): {answers.get('horizon', '')}"],
        )

        phase = space_path / "CURRENT_PHASE.md"
        self._append_markdown_section(
            phase,
            f"Intake {date_label}",
            [
                f"- [ ] Execute: {answers.get('horizon', '')}",
                f"- [ ] Reduce constraint: {answers.get('constraint', '')}",
                f"- [ ] Validate assumption: {answers.get('assumption', '')}",
            ],
        )

        return space_path

    async def _handle_intake(
        self,
        chat_id: int,
        user_id: int,
        text: str = "",
        message_id: int | None = None,
    ) -> None:
        from navig.commands.space import get_active_space
        from navig.spaces.contracts import normalize_space_name, validate_space_name
        from navig.store.runtime import get_runtime_store

        raw = (text or "").strip()
        arg = raw[len("/intake") :].strip() if raw.lower().startswith("/intake") else ""
        if arg.lower() in {"stop", "cancel"}:
            store = get_runtime_store()
            state = store.get_ai_state(user_id) or {}
            context = dict(state.get("context") or {})
            context["intake"] = {"active": False}
            self._runtime_state_with_context(user_id, chat_id, context)
            if message_id:
                await self.edit_message(
                    chat_id,
                    message_id,
                    "🛑 Intake cancelled.",
                    parse_mode=None,
                    keyboard=[[{"text": "🏠 Home", "callback_data": "nav:home"}]],
                )
            else:
                await self.send_message(chat_id, "🛑 Intake cancelled.", parse_mode=None)
            return

        selected_space = get_active_space()
        if arg:
            if not validate_space_name(arg):
                await self.send_message(
                    chat_id, "Unknown space for intake. Use <code>/spaces</code> first.", parse_mode="HTML"
                )
                return
            selected_space = normalize_space_name(arg)

        store = get_runtime_store()
        state = store.get_ai_state(user_id) or {}
        context = dict(state.get("context") or {})
        context["intake"] = {
            "active": True,
            "space": selected_space,
            "step": 0,
            "answers": {},
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self._runtime_state_with_context(user_id, chat_id, context)

        first_question = self._INTAKE_QUESTIONS[0][1]
        text_payload = f"🧭 Intake started for <code>{html.escape(selected_space)}</code>.\n{first_question}"
        keyboard = [
            [
                {"text": "🔙 Back", "callback_data": "nav:back"},
                {"text": "❌ Cancel", "callback_data": "nav:cancel"},
            ],
        ]
        if message_id:
            if await self.edit_message(
                chat_id, message_id, text_payload, parse_mode="HTML", keyboard=keyboard
            ) is not None:
                return
        sent = await self.send_message(
            chat_id, text_payload, parse_mode="HTML", keyboard=keyboard
        )
        if sent and isinstance(sent, dict):
            self._get_navigation_state(chat_id)["message_id"] = sent.get("message_id")

    async def _handle_intake_reply(self, chat_id: int, user_id: int, text: str) -> bool:
        from navig.spaces.kickoff import build_space_kickoff
        from navig.store.runtime import get_runtime_store

        if not text or text.strip().startswith("/"):
            return False

        store = get_runtime_store()
        state = store.get_ai_state(user_id) or {}
        context = dict(state.get("context") or {})
        intake = dict(context.get("intake") or {})
        if not intake.get("active"):
            return False

        step = int(intake.get("step") or 0)
        answers = dict(intake.get("answers") or {})
        if step >= len(self._INTAKE_QUESTIONS):
            context["intake"] = {"active": False}
            self._runtime_state_with_context(user_id, chat_id, context)
            return True

        key, _ = self._INTAKE_QUESTIONS[step]
        answers[key] = text.strip()

        next_step = step + 1
        if next_step < len(self._INTAKE_QUESTIONS):
            intake["step"] = next_step
            intake["answers"] = answers
            context["intake"] = intake
            self._runtime_state_with_context(user_id, chat_id, context)
            await self.send_message(chat_id, self._INTAKE_QUESTIONS[next_step][1], parse_mode=None)
            return True

        space = str(intake.get("space") or "life")
        space_path = self._apply_intake_to_space_docs(space, answers)
        kickoff = build_space_kickoff(space, space_path, cwd=Path.cwd(), max_items=3)

        context["intake"] = {
            "active": False,
            "space": space,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        self._runtime_state_with_context(user_id, chat_id, context)

        lines = [
            f"✅ Intake completed for `{space}`.",
            f"Updated: {space_path / 'VISION.md'}, {space_path / 'ROADMAP.md'}, {space_path / 'CURRENT_PHASE.md'}",
        ]
        if kickoff.actions:
            lines.append("Top next actions:")
            for index, action in enumerate(kickoff.actions, start=1):
                lines.append(f"{index}. {action}")
        await self.send_message(chat_id, "\n".join(lines), parse_mode=None)
        return True

    def _detect_space_from_text(self, text: str) -> str | None:
        from navig.spaces.contracts import CANONICAL_SPACES, SPACE_ALIASES, normalize_space_name

        lowered = (text or "").lower()
        for name in CANONICAL_SPACES:
            if re.search(rf"\b{re.escape(name)}\b", lowered):
                return name

        for alias in SPACE_ALIASES:
            if re.search(rf"\b{re.escape(alias)}\b", lowered):
                return normalize_space_name(alias)

        for hint, mapped in self._NL_DOMAIN_HINTS.items():
            if re.search(rf"\b{re.escape(hint)}\b", lowered):
                return mapped
        return None

    def _infer_nl_space_intent(self, text: str) -> tuple[str | None, str | None]:
        lowered = (text or "").strip().lower()
        if not lowered or lowered.startswith("/"):
            return None, None

        detected_space = self._detect_space_from_text(lowered)
        has_switch_signal = any(verb in lowered for verb in self._NL_SWITCH_VERBS)
        has_intake_signal = any(verb in lowered for verb in self._NL_INTAKE_VERBS)

        if detected_space and has_switch_signal and not has_intake_signal:
            return "space", detected_space

        if detected_space and has_intake_signal:
            return "intake", detected_space

        if has_intake_signal and detected_space:
            return "intake", detected_space

        return None, None

    @staticmethod
    def _nl_phrase_aliases() -> dict[str, tuple[str, ...]]:
        return {
            "status": ("system status", "show status", "check status"),
            "help": ("help", "what can you do", "show commands"),
            "hosts": ("list hosts", "show hosts", "configured hosts"),
            "models": ("model routing", "show models", "routing table"),
            "providers": ("show providers", "provider hub"),
            "spaces": ("list spaces", "show spaces"),
            "myreminders": ("my reminders", "show reminders", "list reminders"),
            "cancelreminder": ("cancel reminder", "remove reminder"),
            "voiceon": ("enable voice", "turn voice on"),
            "voiceoff": ("disable voice", "turn voice off"),
            "restart": ("restart daemon", "restart service", "restart container"),
            "trace": ("show trace", "trace debug", "recent trace"),
            "briefing": ("daily briefing", "show briefing"),
            "ping": ("ping", "heartbeat", "alive check"),
            "weather": ("weather", "weather in"),
            "remindme": ("remind me", "set reminder"),
            "choice": ("choose between", "pick one", "make a choice"),
            "skill": ("run skill", "list skills", "skill list"),
            "db": ("list databases", "show databases"),
        }

    def _extract_nl_args(self, raw_text: str, phrase: str) -> str:
        match = re.search(re.escape(phrase), raw_text, flags=re.IGNORECASE)
        if not match:
            return ""
        tail = raw_text[match.end() :].strip()
        tail = re.sub(r"^(for|to|with|in|on)\s+", "", tail, flags=re.IGNORECASE)
        return tail.strip(" .,!?:;")

    def _resolve_nl_command_intent(self, text: str) -> dict[str, Any] | None:
        lowered = (text or "").strip().lower()
        if not lowered or lowered.startswith("/"):
            return None

        has_trigger = any(
            re.search(rf"\b{re.escape(w)}\b", lowered) for w in self._NL_COMMAND_TRIGGERS
        )
        alias_map = self._nl_phrase_aliases()

        best: dict[str, Any] | None = None
        candidates: list[dict[str, Any]] = []

        for entry in _iter_unique_registry():
            command = entry.command
            phrases = {command, command.replace("_", " ")}
            phrases.update(alias_map.get(command, ()))

            for phrase in sorted(phrases, key=len, reverse=True):
                if not phrase:
                    continue
                if not re.search(rf"\b{re.escape(phrase)}\b", lowered):
                    continue

                starts = lowered.startswith(phrase)
                if command in self._NL_AMBIGUOUS_COMMANDS and not starts and not has_trigger:
                    continue

                args = self._extract_nl_args(text, phrase)
                score = len(phrase) + (4 if starts else 0) + (1 if has_trigger else 0)

                candidate = {
                    "command": command,
                    "args": args,
                    "risk": "risky" if command in self._NL_RISKY_COMMANDS else "safe",
                    "usage": entry.usage or f"/{command}",
                    "score": score,
                }
                candidates.append(candidate)
                if not best or score > int(best.get("score", 0)):
                    best = candidate

        if not best:
            return None

        # If top two commands tie, ask user to choose instead of guessing.
        ranked = sorted(candidates, key=lambda x: int(x.get("score", 0)), reverse=True)
        if len(ranked) > 1:
            first = ranked[0]
            second = ranked[1]
            if int(first.get("score", 0)) == int(second.get("score", 0)) and str(
                first.get("command") or ""
            ) != str(second.get("command") or ""):
                return {
                    "ambiguous": True,
                    "candidates": [first, second, *ranked[2:4]],
                }

        command = str(best.get("command") or "")
        args = str(best.get("args") or "")
        if command in self._NL_REQUIRED_ARGS_COMMANDS and not args:
            best["missing_args"] = True
        return best

    def _suggest_nl_commands(self, text: str, limit: int = 3) -> list[dict[str, str]]:
        lowered = (text or "").lower()
        tokens = set(re.findall(r"[a-z0-9_]+", lowered))
        alias_map = self._nl_phrase_aliases()
        scored: list[tuple[int, str, str]] = []

        for entry in _iter_unique_registry(visible_only=True):
            command = entry.command
            usage = entry.usage or f"/{command}"
            phrases = {command, command.replace("_", " ")}
            phrases.update(alias_map.get(command, ()))

            score = 0
            for phrase in phrases:
                p_tokens = set(re.findall(r"[a-z0-9_]+", phrase.lower()))
                if not p_tokens:
                    continue
                overlap = len(tokens & p_tokens)
                if overlap:
                    score = max(score, overlap)
                if phrase and phrase.lower() in lowered:
                    score = max(score, 3)

            if score > 0:
                scored.append((score, command, usage))

        scored.sort(key=lambda row: (-row[0], row[1]))
        suggestions = [{"command": c, "usage": u} for _, c, u in scored[:limit]]

        if not suggestions:
            # Command-first fallback suggestions for action-oriented messages.
            suggestions = [
                {"command": "status", "usage": "/status"},
                {"command": "help", "usage": "/help"},
                {"command": "hosts", "usage": "/hosts"},
            ][:limit]
        return suggestions

    def _nl_command_keyboard(
        self,
        commands: list[dict[str, Any]],
        *,
        limit: int = 3,
    ) -> list[list[dict[str, str]]]:
        rows: list[list[dict[str, str]]] = []
        for item in commands[:limit]:
            command = str(item.get("command") or "").strip().lower()
            usage = str(item.get("usage") or f"/{command}").strip()
            if not command:
                continue
            rows.append(
                [
                    {
                        "text": f"▶ {usage}",
                        "callback_data": f"nl_pick:{command}",
                    }
                ]
            )
        if rows:
            rows.append([{"text": "🛑 Cancel", "callback_data": "nl_cancel"}])
        return rows

    async def _queue_nl_risky_command_confirmation(
        self,
        chat_id: int,
        user_id: int,
        command: str,
        args: str,
    ) -> None:
        from navig.store.runtime import get_runtime_store

        store = get_runtime_store()
        state = store.get_ai_state(user_id) or {}
        context = dict(state.get("context") or {})
        pending_id = datetime.now(timezone.utc).isoformat()
        context["nl_pending"] = {
            "active": True,
            "id": pending_id,
            "intent": "command",
            "kind": "command",
            "command": command,
            "args": args,
            "created_at": pending_id,
        }
        self._runtime_state_with_context(user_id, chat_id, context)

        preview = f"/{command}" + (f" {args}" if args else "")
        await self.send_message(
            chat_id,
            (
                "⚠️ Risky action detected from natural language.\n"
                f"Planned command: <code>{preview}</code>\n"
                "Reply <code>yes</code> to run now or <code>cancel</code> to stop."
            ),
            parse_mode="HTML",
            keyboard=[
                [
                    {"text": "✅ Yes now", "callback_data": "nl_yes"},
                    {"text": "🛑 Cancel", "callback_data": "nl_cancel"},
                ]
            ],
        )

    async def _handle_nl_command_pick(
        self,
        chat_id: int,
        user_id: int,
        command: str,
    ) -> str:
        cmd = (command or "").strip().lower()
        entry = next((e for e in _iter_unique_registry() if e.command == cmd), None)
        if not cmd or not entry:
            await self.send_message(chat_id, "Command not available.", parse_mode=None)
            return "⚠️ Command unavailable"

        usage = entry.usage or f"/{cmd}"
        if cmd in self._NL_REQUIRED_ARGS_COMMANDS:
            await self.send_message(
                chat_id,
                f"This command needs arguments.\nUsage: <code>{usage}</code>",
                parse_mode="HTML",
            )
            return "ℹ️ Needs arguments"

        if cmd in self._NL_RISKY_COMMANDS:
            await self._queue_nl_risky_command_confirmation(
                chat_id=chat_id,
                user_id=user_id,
                command=cmd,
                args="",
            )
            return "⚠️ Confirmation required"

        await self._execute_nl_registry_command(
            chat_id=chat_id,
            user_id=user_id,
            command=cmd,
            args="",
            text=f"/{cmd}",
        )
        return f"✅ Running /{cmd}"

    async def _execute_nl_registry_command(
        self,
        chat_id: int,
        user_id: int,
        command: str,
        args: str,
        text: str,
        *,
        is_group: bool = False,
        username: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        import inspect

        cmd = (command or "").strip().lower()
        if not cmd:
            return

        business_chat_only = {"kick", "mute", "unmute", "search"}
        if cmd in business_chat_only:
            if not is_group:
                await self.send_message(
                    chat_id,
                    "This command is only available in business chats (groups/supergroups).",
                    parse_mode=None,
                )
                return
            if hasattr(self, "_is_group_admin") and not await self._is_group_admin(
                chat_id, user_id
            ):
                await self.send_message(
                    chat_id,
                    "You need group admin rights for this command.",
                    parse_mode=None,
                )
                return

        slash_text = f"/{cmd}" + (f" {args}" if args else "")

        entry = next((e for e in _iter_unique_registry() if e.command == cmd), None)
        if not entry:
            await self.send_message(chat_id, "Command not available.", parse_mode=None)
            return

        if entry.handler:
            method = getattr(self, entry.handler, None)
            if method is not None:
                call_ctx = {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "username": username,
                    "metadata": metadata or {},
                    "is_group": is_group,
                    "text": slash_text,
                }
                try:
                    sig = inspect.signature(method)
                    kwargs = {k: v for k, v in call_ctx.items() if k in sig.parameters}
                except (ValueError, TypeError):
                    kwargs = {"chat_id": chat_id}
                await method(**kwargs)
                return

        navig_cmd = self._match_cli_command(slash_text)
        if navig_cmd:
            await self._handle_cli_command(chat_id, user_id, metadata or {}, navig_cmd)
            return

        await self.send_message(
            chat_id,
            f"This command is not executable via natural language yet: <code>/{cmd}</code>",
            parse_mode="HTML",
        )

    async def _handle_natural_language_request(
        self,
        chat_id: int,
        user_id: int,
        text: str,
        is_group: bool = False,
        username: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        from navig.store.runtime import get_runtime_store

        intent, space = self._infer_nl_space_intent(text)
        if intent and space:
            store = get_runtime_store()
            state = store.get_ai_state(user_id) or {}
            context = dict(state.get("context") or {})
            pending_id = datetime.now(timezone.utc).isoformat()
            context["nl_pending"] = {
                "active": True,
                "id": pending_id,
                "intent": intent,
                "space": space,
                "created_at": pending_id,
            }
            self._runtime_state_with_context(user_id, chat_id, context)

            await self.send_message(
                chat_id,
                (
                    f"🧭 Detected <code>{intent}</code> for <code>{space}</code>. "
                    "Auto-starting in 3s. Reply <code>cancel</code> to stop or <code>yes</code> to run now."
                ),
                parse_mode="HTML",
                keyboard=[
                    [
                        {"text": "✅ Yes now", "callback_data": "nl_yes"},
                        {"text": "🛑 Cancel", "callback_data": "nl_cancel"},
                    ]
                ],
            )
            asyncio.create_task(
                self._execute_nl_pending_after_delay(
                    chat_id=chat_id,
                    user_id=user_id,
                    pending_id=pending_id,
                    delay_seconds=3,
                )
            )
            return True

        # ── Multilingual reminder short-circuit (runs before English-only alias map) ──
        reminder_pseudo = self._sniff_reminder_intent(text)
        if reminder_pseudo:
            await self._handle_remindme(chat_id, user_id, reminder_pseudo)
            return True

        resolved = self._resolve_nl_command_intent(text)
        if not resolved:
            lowered = (text or "").strip().lower()
            has_trigger = any(
                re.search(rf"\b{re.escape(w)}\b", lowered) for w in self._NL_COMMAND_TRIGGERS
            )
            if has_trigger:
                suggestions = self._suggest_nl_commands(text, limit=3)
                if suggestions:
                    lines = [
                        "I couldn’t map that to one exact command.",
                        "Tap a command below to run it.",
                        "",
                        "Try:",
                    ]
                    for item in suggestions:
                        lines.append(f"• <code>{item['usage']}</code>")
                    await self.send_message(
                        chat_id,
                        "\n".join(lines),
                        parse_mode="HTML",
                        keyboard=self._nl_command_keyboard(suggestions, limit=3),
                    )
                    return True
            return False

        if resolved.get("ambiguous"):
            candidates = list(resolved.get("candidates") or [])[:3]
            if candidates:
                lines = [
                    "I found multiple matching commands.",
                    "Tap a command below to run it.",
                    "",
                    "Pick one:",
                ]
                for candidate in candidates:
                    usage = str(candidate.get("usage") or f"/{candidate.get('command')}")
                    lines.append(f"• <code>{usage}</code>")
                await self.send_message(
                    chat_id,
                    "\n".join(lines),
                    parse_mode="HTML",
                    keyboard=self._nl_command_keyboard(candidates, limit=3),
                )
                return True
            return False

        command = str(resolved.get("command") or "")
        args = str(resolved.get("args") or "")
        usage = str(resolved.get("usage") or f"/{command}")

        if resolved.get("missing_args"):
            await self.send_message(
                chat_id,
                f"I can run this as <code>/{command}</code>, but it needs arguments.\nUsage: <code>{usage}</code>",
                parse_mode="HTML",
            )
            return True

        if str(resolved.get("risk") or "safe") == "risky":
            await self._queue_nl_risky_command_confirmation(
                chat_id=chat_id,
                user_id=user_id,
                command=command,
                args=args,
            )
            return True

        await self._execute_nl_registry_command(
            chat_id=chat_id,
            user_id=user_id,
            command=command,
            args=args,
            text=text,
            is_group=is_group,
            username=username,
            metadata=metadata,
        )
        return True

    async def _run_nl_intent(
        self,
        chat_id: int,
        user_id: int,
        intent: str,
        space: str,
        *,
        command: str = "",
        args: str = "",
        is_group: bool = False,
        username: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if intent == "space":
            await self._handle_space(chat_id=chat_id, user_id=user_id, text=f"/space {space}")
            return
        if intent == "intake":
            await self._handle_intake(chat_id=chat_id, user_id=user_id, text=f"/intake {space}")
            return
        if intent == "command":
            await self._execute_nl_registry_command(
                chat_id=chat_id,
                user_id=user_id,
                command=command,
                args=args,
                text="",
                is_group=is_group,
                username=username,
                metadata=metadata,
            )

    async def _handle_pending_api_key_input(self, chat_id: int, user_id: int, text: str) -> bool:
        """Handle a pending API key reply prompted by the prov_cfg_ callback.

        Returns True if the message was consumed (key stored, cancelled, or
        invalid — any case where normal dispatch should be suppressed).
        """
        pending_map: dict = getattr(self, "_pending_api_key_input", {})
        entry = pending_map.get(user_id)
        if not entry:
            return False
        # Skip slash commands so /cancel, /provider etc. still work normally
        if text.strip().startswith("/"):
            return False

        lowered = text.strip().lower()
        prompt_msg_id = entry.get("prompt_msg_id")

        async def _delete_prompt() -> None:
            if prompt_msg_id:
                try:
                    await self._api_call(
                        "deleteMessage",
                        {"chat_id": chat_id, "message_id": prompt_msg_id},
                    )
                except Exception:  # noqa: BLE001
                    pass

        if lowered in {"cancel", "stop", "abort", "no"}:
            pending_map.pop(user_id, None)
            await _delete_prompt()
            await self.send_message(chat_id, "🛑 API key entry cancelled.", parse_mode=None)
            return True

        api_key = text.strip()
        if len(api_key) < 10:
            await self.send_message(
                chat_id,
                "⚠️ That doesn't look like a valid API key (too short). Try again or send `cancel`.",
                parse_mode=None,
            )
            return True

        provider_id = entry.get("provider", "")
        pending_map.pop(user_id, None)
        await _delete_prompt()

        # Store key in vault
        try:
            from navig.vault import get_vault

            get_vault().add(
                provider_id,
                "api_key",
                {"api_key": api_key},
                profile_id="default",
                label=f"{provider_id.title()} Key",
            )
            logger.info("API key for %s stored in vault via Telegram", provider_id)
        except Exception as exc:
            logger.warning("Failed to store API key for %s in vault: %s", provider_id, exc)
            await self.send_message(
                chat_id,
                f"⚠️ Could not save API key: {exc}\nTry `navig init` via CLI to set it.",
                parse_mode=None,
            )
            return True

        # Trigger provider activation with the newly saved key
        try:
            from navig.providers.registry import get_provider

            manifest = get_provider(provider_id)
            cb_handler = getattr(self, "_cb_handler", None)
            if manifest and cb_handler is not None:
                await cb_handler._activate_provider_with_defaults(
                    "", chat_id, 0, provider_id, manifest
                )
            else:
                self._update_llm_mode_router(
                    provider_id, {"small": "", "big": "", "coder_big": ""}
                )
                prov_name_disp = manifest.display_name if manifest else provider_id
                await self.send_message(
                    chat_id,
                    f"✅ API key for <b>{html.escape(prov_name_disp)}</b> saved. Use /provider to activate it.",
                    parse_mode="HTML",
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Post-key-entry activation failed for %s: %s", provider_id, exc)
            await self.send_message(
                chat_id,
                f"✅ API key saved. Use /provider to activate <b>{provider_id}</b>.",
                parse_mode="HTML",
            )

        return True

    async def _handle_nl_pending_reply(self, chat_id: int, user_id: int, text: str) -> bool:
        from navig.store.runtime import get_runtime_store

        if not text or text.strip().startswith("/"):
            return False

        store = get_runtime_store()
        state = store.get_ai_state(user_id) or {}
        context = dict(state.get("context") or {})
        pending = dict(context.get("nl_pending") or {})
        if not pending.get("active"):
            return False

        lowered = text.strip().lower()
        if lowered in {"cancel", "stop", "no", "abort"}:
            context["nl_pending"] = {"active": False}
            self._runtime_state_with_context(user_id, chat_id, context)
            await self.send_message(
                chat_id, "🛑 Natural-language action cancelled.", parse_mode=None
            )
            return True

        if lowered in {"yes", "ok", "go", "proceed", "run"}:
            intent = str(pending.get("intent") or "")
            space = str(pending.get("space") or "")
            command = str(pending.get("command") or "")
            args = str(pending.get("args") or "")
            context["nl_pending"] = {"active": False}
            self._runtime_state_with_context(user_id, chat_id, context)
            await self._run_nl_intent(
                chat_id=chat_id,
                user_id=user_id,
                intent=intent,
                space=space,
                command=command,
                args=args,
            )
            return True

        await self.send_message(
            chat_id,
            "Pending natural-language action. Reply <code>yes</code> to run now or <code>cancel</code> to stop.",
            parse_mode="HTML",
        )
        return True

    async def _execute_nl_pending_after_delay(
        self,
        chat_id: int,
        user_id: int,
        pending_id: str,
        delay_seconds: int = 3,
    ) -> None:
        from navig.store.runtime import get_runtime_store

        await asyncio.sleep(max(0, delay_seconds))
        store = get_runtime_store()
        state = store.get_ai_state(user_id) or {}
        context = dict(state.get("context") or {})
        pending = dict(context.get("nl_pending") or {})
        if not pending.get("active"):
            return
        if str(pending.get("id") or "") != pending_id:
            return

        intent = str(pending.get("intent") or "")
        space = str(pending.get("space") or "")
        command = str(pending.get("command") or "")
        args = str(pending.get("args") or "")
        context["nl_pending"] = {"active": False}
        self._runtime_state_with_context(user_id, chat_id, context)
        await self._run_nl_intent(
            chat_id=chat_id,
            user_id=user_id,
            intent=intent,
            space=space,
            command=command,
            args=args,
        )

    async def _handle_nl_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        user_id: int,
    ) -> None:
        if cb_data.startswith("nl_pick:"):
            command = cb_data.split(":", 1)[1].strip().lower()
            ack_text = await self._handle_nl_command_pick(chat_id, user_id, command)
            await self._api_call(
                "answerCallbackQuery",
                {
                    "callback_query_id": cb_id,
                    "text": ack_text,
                    "show_alert": False,
                },
            )
            return

        if cb_data == "nl_yes":
            await self._handle_nl_pending_reply(chat_id, user_id, "yes")
            await self._api_call(
                "answerCallbackQuery",
                {
                    "callback_query_id": cb_id,
                    "text": "✅ Running now",
                    "show_alert": False,
                },
            )
            return

        await self._handle_nl_pending_reply(chat_id, user_id, "cancel")
        await self._api_call(
            "answerCallbackQuery",
            {
                "callback_query_id": cb_id,
                "text": "🛑 Cancelled",
                "show_alert": False,
            },
        )

    async def _handle_user(self, chat_id: int, user_id: int, username: str) -> None:
        """Show user profile, preferences, and session state (/user)."""
        is_group = chat_id != user_id

        # ── Identity ──────────────────────────────────────────────────────
        header = "👤 <b>User Profile</b>"
        if username:
            header += f" — @{username}"
        lines: list[str] = [header, ""]
        lines.append(f"🆔 User ID: <code>{user_id}</code>")
        if is_group:
            lines.append(f"💬 Chat ID: <code>{chat_id}</code> <i>(group)</i>")

        # ── Auth ──────────────────────────────────────────────────────────
        if getattr(self, "allowed_users", None):
            is_allowed = user_id in self.allowed_users
            auth_label = f"{_ni('tick')} Allowed" if is_allowed else "⛔ Not in allowed list"
        else:
            auth_label = f"{_ni('tick')} Open access"
        lines.append(f"{_ni('auth')} Auth: {auth_label}")

        # ── AI settings ───────────────────────────────────────────────────
        lines.append("")
        lines.append("<b>AI Settings</b>")

        # Model tier preference
        if hasattr(self, "_get_user_tier_pref"):
            tier = self._get_user_tier_pref(chat_id, user_id)
        else:
            tier = (getattr(self, "_user_model_prefs", {}) or {}).get(user_id, "")
        lines.append(f"{_ni('brain')} Model tier: <code>{tier or 'auto'}</code>")

        # Active provider from LLM router
        active_prov = ""
        try:
            from navig.llm_router import get_llm_router

            lr = get_llm_router()
            if lr:
                _m = lr.modes.get_mode("big_tasks")
                if _m and getattr(_m, "provider", None):
                    active_prov = _m.provider
        except Exception:  # noqa: BLE001
            pass
        lines.append(f"🤖 Provider: <code>{active_prov or 'not set'}</code>")

        # ── Preferences (from session metadata) ───────────────────────────
        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                session = sm.get_or_create_session(chat_id, user_id, is_group=is_group)
                voice_on = sm.get_session_metadata(
                    chat_id, user_id, "voice_replies_enabled", False, is_group=is_group
                )
                focus = sm.get_session_metadata(
                    chat_id, user_id, "focus_mode", "balance", is_group=is_group
                )
                msg_count = getattr(session, "message_count", 0)
                lines.append("")
                lines.append("<b>Preferences</b>")
                lines.append(f"{_ni('voice')} Voice replies: {'🔊 on' if voice_on else '🔇 off'}")
                lines.append(f"{_ni('focus')} Focus mode: <code>{focus}</code>")
                if msg_count:
                    lines.append(f"{_ni('note')} Messages in session: {msg_count}")
            except Exception as _e:  # noqa: BLE001
                logger.debug("_handle_user session fetch failed: %s", _e)

        # Debug mode flag
        if getattr(self, "_debug_users", None) and user_id in self._debug_users:
            lines.append(f"{_ni('debug')} Debug mode: on")

        # ── Inline keyboard ───────────────────────────────────────────────
        keyboard = [
            [
                {"text": "⚙️ Settings", "callback_data": "st_goto_settings"},
                {"text": "🔊 Voice", "callback_data": "st_goto_voice"},
                {"text": "🎯 Focus", "callback_data": "st_goto_focus"},
            ],
            [
                {"text": "🤖 Provider", "callback_data": "st_goto_providers"},
                {"text": "📝 Models", "callback_data": "st_goto_model"},
                {"text": "📊 Status", "callback_data": "helpme"},
            ],
            [{"text": "✖ Close", "callback_data": "prov_close"}],
        ]
        await self.send_message(chat_id, "\n".join(lines), parse_mode="HTML", keyboard=keyboard)

    async def _handle_mode(self, chat_id: int, text: str = "", user_id: int = 0) -> None:
        """Set focus/behavior mode. Uses MOOD_REGISTRY with fuzzy matching."""
        mode_arg = (
            text[len("/mode") :].strip() if text.lower().startswith("/mode") else text.strip()
        )
        from navig.agent.soul import MOOD_REGISTRY, get_mood_profile

        _uid = user_id or chat_id

        if not mode_arg or mode_arg in ("help", "list"):
            lines = ["<b>Focus Modes</b>\n"]
            current = "balance"
            if _HAS_SESSIONS:
                try:
                    sm = get_session_manager()
                    _is_grp = chat_id != _uid
                    current = sm.get_session_metadata(
                        chat_id, _uid, "focus_mode", "balance", is_group=_is_grp
                    )
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
            for mid, mp in MOOD_REGISTRY.items():
                active_marker = " <b>- active</b>" if mid == current else ""
                lines.append(f"{mp.emoji} <code>{mid}</code>{active_marker}\n<i>{mp.character}</i>")
            lines.append(
                "\n<code>/mode &lt;name&gt;</code> to switch  -  "
                "<code>/mode auto</code> to let NAVIG decide"
            )
            await self.send_message(chat_id, "\n\n".join(lines), parse_mode="HTML")
            return

        if mode_arg.lower() == "auto":
            if _HAS_SESSIONS:
                try:
                    sm = get_session_manager()
                    sm.set_session_metadata(
                        chat_id,
                        _uid,
                        "focus_mode",
                        "balance",
                        is_group=chat_id != _uid,
                    )
                except Exception as e:
                    logger.debug("Failed to clear focus mode: %s", e)
            try:
                from navig.agent.proactive.user_state import get_user_state_tracker

                get_user_state_tracker().set_preference("chat_mode", "auto")
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
            await self.send_message(chat_id, "- auto. reading the room.", parse_mode=None)
            return

        try:
            mood = get_mood_profile(mode_arg)
        except KeyError:
            available = "  ".join(f"<code>{k}</code>" for k in MOOD_REGISTRY)
            await self.send_message(
                chat_id,
                f"Unknown mode: <code>{html.escape(mode_arg)}</code>\n\nAvailable: {available}",
                parse_mode="HTML",
            )
            return

        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                sm.set_session_metadata(
                    chat_id,
                    _uid,
                    "focus_mode",
                    mood.id,
                    is_group=chat_id != _uid,
                )
            except Exception as e:
                logger.debug("Failed to persist focus_mode: %s", e)
        try:
            from navig.agent.proactive.user_state import get_user_state_tracker

            get_user_state_tracker().set_preference("chat_mode", mood.id)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        await self.send_message(chat_id, mood.transition_message, parse_mode=None)

    async def _handle_tier_override(
        self,
        chat_id: int,
        user_id: int = 0,
        text: str = "",
    ) -> None:
        """Handle /big /small /coder /auto from dynamic slash dispatch."""
        token = (text or "").strip().split(" ", 1)[0].lower()
        cmd = token.split("@", 1)[0]
        if cmd not in {"/big", "/small", "/coder", "/auto"}:
            await self.send_message(
                chat_id,
                "Use <code>/big</code>, <code>/small</code>, <code>/coder</code>, or <code>/auto</code>.",
                parse_mode="HTML",
            )
            return
        if not hasattr(self, "_handle_tier_command"):
            await self.send_message(
                chat_id,
                "Tier command handler unavailable in this channel build.",
                parse_mode=None,
            )
            return
        await self._handle_tier_command(chat_id, user_id, cmd)

    async def _handle_voiceon_cmd(
        self,
        chat_id: int,
        user_id: int = 0,
        is_group: bool = False,
    ) -> None:
        """Enable voice replies from dynamic slash dispatch."""
        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                sm.set_session_metadata(
                    chat_id, user_id, "voice_replies_enabled", True, is_group=is_group
                )
            except Exception as _e:  # noqa: BLE001
                logger.debug("_handle_voiceon_cmd session update failed: %s", _e)
        await self.send_message(chat_id, "🔊 Voice replies enabled.", parse_mode=None)

    async def _handle_voiceoff_cmd(
        self,
        chat_id: int,
        user_id: int = 0,
        is_group: bool = False,
    ) -> None:
        """Disable voice replies from dynamic slash dispatch."""
        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                sm.set_session_metadata(
                    chat_id, user_id, "voice_replies_enabled", False, is_group=is_group
                )
            except Exception as _e:  # noqa: BLE001
                logger.debug("_handle_voiceoff_cmd session update failed: %s", _e)
        await self.send_message(
            chat_id,
            "🔇 Voice replies disabled. You'll receive text only.",
            parse_mode=None,
        )

    async def _handle_autoheal(
        self,
        chat_id: int,
        user_id: int = 0,
        text: str = "",
    ) -> None:
        """Delegate /autoheal to AutoHealMixin (TelegramChannel is a flat class)."""
        try:
            from navig.gateway.channels.telegram_autoheal import AutoHealMixin

            await AutoHealMixin._handle_autoheal(self, chat_id, user_id, text)
        except Exception as _e:  # noqa: BLE001
            logger.debug("_handle_autoheal delegation failed: %s", _e)
            await self.send_message(
                chat_id,
                "⚠️ Auto-Heal module unavailable in this build.",
                parse_mode=None,
            )

    async def _handle_trace_cmd(
        self,
        chat_id: int,
        user_id: int = 0,
        text: str = "",
    ) -> None:
        """Handle /trace + /trace debug on|off from dynamic slash dispatch."""
        trace_arg = text.strip()[len("/trace") :].strip().lower() if text else ""
        if trace_arg in ("debug on", "debug"):
            self._debug_users.add(user_id)
            await self.send_message(
                chat_id,
                "🔍 Debug mode <b>ON</b> — model names will appear in every response.\n"
                "Run <code>/trace debug off</code> to disable.",
                parse_mode="HTML",
            )
            return
        if trace_arg == "debug off":
            self._debug_users.discard(user_id)
            await self.send_message(
                chat_id,
                "🔍 Debug mode <b>OFF</b> — model footers hidden.",
                parse_mode="HTML",
            )
            return
        await self._handle_trace(chat_id, user_id)

    async def _handle_restart_cmd(
        self,
        chat_id: int,
        user_id: int = 0,
        text: str = "",
        metadata: dict | None = None,
    ) -> None:
        """Delegate /restart from dynamic slash dispatch to TelegramChannel implementation."""
        restart_arg = text.strip()[len("/restart") :].strip() if text else ""
        if not restart_arg:
            restart_arg = "daemon"
        if hasattr(self, "_handle_restart"):
            await self._handle_restart(chat_id, user_id, metadata or {}, restart_arg)
            return
        await self.send_message(chat_id, "Restart handler unavailable.", parse_mode=None)

    async def _handle_skill_cmd(
        self,
        chat_id: int,
        user_id: int = 0,
        text: str = "",
        metadata: dict | None = None,
    ) -> None:
        """Delegate /skill from dynamic slash dispatch to TelegramChannel implementation."""
        skill_arg = text.strip()[len("/skill") :].strip() if text else ""
        if hasattr(self, "_handle_skill"):
            await self._handle_skill(chat_id, user_id, skill_arg, metadata or {})
            return
        await self.send_message(chat_id, "Skill handler unavailable.", parse_mode=None)

    # -- Model routing UI ------------------------------------------------------

    async def _probe_bridge_grid(self) -> tuple[bool, str]:
        """Return ``(is_online, display_url)``.  Reads bridge-registry.json first."""
        import socket as _sock
        from pathlib import Path as _P

        try:
            reg_path = _P.home() / ".navig" / "bridge-registry.json"
            if reg_path.exists():
                data = json.loads(reg_path.read_text(encoding="utf-8"))
                entry = data.get("bridge_copilot") or {}
                url = entry.get("url", "")
                if url:
                    import asyncio
                    from urllib.parse import urlparse

                    parsed = urlparse(url)
                    host = parsed.hostname or "127.0.0.1"
                    port = parsed.port or 11435
                    try:
                        reader, writer = await asyncio.wait_for(
                            asyncio.open_connection(host, port), timeout=1.5
                        )
                        writer.close()
                        await writer.wait_closed()
                        return True, f"{host}:{port}"
                    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                        return False, f"{host}:{port}"
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        try:
            from navig.providers.bridge_grid_reader import (
                BRIDGE_DEFAULT_PORT,
                get_llm_port,
            )

            port = get_llm_port() or BRIDGE_DEFAULT_PORT
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
            s.settimeout(0.5)
            ok = s.connect_ex(("127.0.0.1", port)) == 0
            s.close()
            return ok, f"127.0.0.1:{port}"
        except Exception:
            return False, "not configured"

    async def _handle_models_command(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
        text: str = "",
    ) -> None:
        """Interactive model configuration (/models).

        Auto-detects active provider → shows tier summary.
        If no provider is configured, shows a provider picker first.

        /models big|small|coder|auto  — quick-switch tier then confirm.
        """
        # Quick-switch: /models big  --  /models auto  etc.
        tier_arg = ""
        for prefix in ("/models", "/model", "/routing", "/router"):
            if text.lower().startswith(prefix):
                tier_arg = text[len(prefix) :].strip().lower()
                break
        if tier_arg in ("big", "small", "coder", "auto"):
            await self._handle_tier_command(chat_id, user_id, f"/{tier_arg}")
            return

        # Detect active provider
        active_prov = ""
        try:
            from navig.llm_router import get_llm_router

            lr = get_llm_router()
            if lr:
                m = lr.modes.get_mode("big_tasks")
                if m and getattr(m, "provider", None):
                    active_prov = m.provider
        except Exception:  # noqa: BLE001
            pass  # best-effort

        if active_prov:
            # Provider is active → show tier summary
            await self._show_models_tier_summary(
                chat_id,
                active_prov,
                message_id=message_id,
            )
        else:
            # No provider → show mini provider picker
            await self._show_models_provider_picker(
                chat_id,
                message_id=message_id,
            )

    async def _show_models_provider_picker(
        self,
        chat_id: int,
        message_id: int | None = None,
    ) -> None:
        """Show a compact provider picker when /models has no active provider."""
        lines = [
            "📝 <b>Configure Models</b>",
            "",
            "No provider is active yet.",
            "Choose a provider to start:",
        ]

        keyboard_rows: list = []
        try:
            from navig.providers.registry import list_enabled_providers
            from navig.providers.verifier import verify_provider

            for manifest in list_enabled_providers():
                if manifest.id == "mcp_bridge":
                    continue
                try:
                    result = verify_provider(manifest)
                    key_detected = bool(getattr(result, "key_detected", False))
                    if manifest.tier == "local" and manifest.local_probe:
                        ready = bool(result.local_probe_ok)
                    elif manifest.requires_key:
                        vault_ok, vault_validated = self._provider_vault_validation_status(manifest)
                        ready = key_detected or vault_ok  # consistent with _handle_providers: key exists in vault
                    else:
                        ready = True
                except Exception:
                    ready = False
                if not ready:
                    continue
                keyboard_rows.append(
                    [
                        {
                            "text": f"{manifest.emoji} {manifest.display_name}",
                            "callback_data": f"mdl_prov_{manifest.id}",
                        }
                    ]
                )
        except Exception:  # noqa: BLE001
            lines.append("")
            lines.append("⚠️ Could not load providers.")

        if not keyboard_rows:
            lines.append("")
            lines.append("ℹ️ No providers are ready. Use /provider to configure one.")

        keyboard_rows.append([{"text": "🎛️ Providers", "callback_data": "mdl_chgprov"}])
        keyboard_rows.append([{"text": "✖ Close", "callback_data": "mdl_close"}])

        text_payload = "\n".join(lines)
        if message_id:
            try:
                await self.edit_message(
                    chat_id,
                    message_id,
                    text_payload,
                    parse_mode="HTML",
                    keyboard=keyboard_rows,
                )
                return
            except Exception:  # noqa: BLE001
                pass
        await self.send_message(
            chat_id, text_payload, parse_mode="HTML", keyboard=keyboard_rows
        )

    async def _show_models_tier_summary(
        self,
        chat_id: int,
        prov_id: str,
        message_id: int | None = None,
    ) -> None:
        """Show per-tier model assignments — tap a tier to change its model."""
        emoji, name = "", prov_id
        try:
            from navig.providers.registry import get_provider as _gp

            manifest = _gp(prov_id)
            if manifest:
                emoji = manifest.emoji
                name = manifest.display_name
        except Exception:  # noqa: BLE001
            pass

        # Get current assignments from LLM Mode Router
        current: dict[str, str] = {"small": "—", "big": "—", "coder_big": "—"}
        try:
            from navig.llm_router import get_llm_router

            lr = get_llm_router()
            if lr:
                # Use class reference: self may be TelegramChannel which does not
                # inherit from TelegramCommandsMixin, so self._TIER_TO_MODES would
                # raise AttributeError and silently leave all tiers as "—".
                for tier, modes in TelegramCommandsMixin._TIER_TO_MODES.items():
                    mc = lr.modes.get_mode(modes[0])
                    if mc and getattr(mc, "model", None):
                        if getattr(mc, "provider", "") == prov_id:
                            model = mc.model
                            current[tier] = model.split("/")[-1].split(":")[-1]
        except Exception:  # noqa: BLE001
            logger.debug("Failed to read LLM mode router for tier summary", exc_info=True)

        lines = [
            f"📝 <b>Models — {emoji} {html.escape(name)}</b>",
            "",
            f"⚡ Small: <code>{current['small']}</code>",
            f"🧠 Big: <code>{current['big']}</code>",
            f"💻 Code: <code>{current['coder_big']}</code>",
            "",
            "Tap a tier to change its model:",
        ]

        keyboard = [
            [{"text": f"⚡ Small — {current['small']}", "callback_data": f"mdl_tier_{prov_id}_s"}],
            [{"text": f"🧠 Big — {current['big']}", "callback_data": f"mdl_tier_{prov_id}_b"}],
            [
                {
                    "text": f"💻 Code — {current['coder_big']}",
                    "callback_data": f"mdl_tier_{prov_id}_c",
                }
            ],
            [{"text": "🎛️ Change provider", "callback_data": "mdl_chgprov"}],
            [{"text": "✖ Close", "callback_data": "mdl_close"}],
        ]

        text_payload = "\n".join(lines)
        if message_id:
            try:
                await self.edit_message(
                    chat_id,
                    message_id,
                    text_payload,
                    parse_mode="HTML",
                    keyboard=keyboard,
                )
                return
            except Exception:  # noqa: BLE001
                pass
        await self.send_message(chat_id, text_payload, parse_mode="HTML", keyboard=keyboard)

    async def _show_models_model_list(
        self,
        chat_id: int,
        prov_id: str,
        tier_code: str,
        page: int = 0,
        message_id: int | None = None,
    ) -> None:
        """Show paginated model list for a tier with ✅ on currently assigned model."""
        PAGE_SIZE = 8

        tier_map: dict[str, tuple[str, str]] = {
            "s": ("small", "⚡ Small"),
            "b": ("big", "🧠 Big"),
            "c": ("coder_big", "💻 Code"),
        }
        if tier_code not in tier_map:
            return
        tier, tier_label = tier_map[tier_code]

        emoji, name = "", prov_id
        try:
            from navig.providers.registry import get_provider as _gp

            manifest = _gp(prov_id)
            if manifest:
                emoji = manifest.emoji
                name = manifest.display_name
        except Exception:  # noqa: BLE001
            manifest = None

        # Resolve models
        models: list = []
        resolution_failed = False
        if hasattr(self, "_resolve_provider_models"):
            try:
                models = await self._resolve_provider_models(prov_id, manifest=manifest)
            except Exception:  # noqa: BLE001
                resolution_failed = True
                logger.warning("Model resolution failed for provider model list: %s", prov_id)

        # Safety net: fall back to static registry list if resolution failed
        if not models and manifest and getattr(manifest, "models", None):
            models = list(manifest.models)

        if not models:
            lines = [
                f"📝 <b>{html.escape(tier_label)} — {emoji} {html.escape(name)}</b>",
                "",
                (
                    "⚠️ Could not load models for this provider right now."
                    if resolution_failed
                    else "⚠️ No models available for this provider."
                ),
            ]
            keyboard = [
                [{"text": "← Back", "callback_data": f"mdl_back_tiers_{prov_id}"}],
                [{"text": "✖ Close", "callback_data": "mdl_close"}],
            ]
            text_payload = "\n".join(lines)
            if message_id:
                try:
                    await self.edit_message(
                        chat_id,
                        message_id,
                        text_payload,
                        parse_mode="HTML",
                        keyboard=keyboard,
                    )
                    return
                except Exception:  # noqa: BLE001
                    pass
            await self.send_message(chat_id, text_payload, parse_mode="HTML", keyboard=keyboard)
            return

        # Detect current model for this tier
        current_model = ""
        try:
            from navig.llm_router import get_llm_router

            lr = get_llm_router()
            if lr:
                # Use class reference (see _show_models_tier_summary for rationale)
                modes = TelegramCommandsMixin._TIER_TO_MODES.get(tier, [])
                if modes:
                    mc = lr.modes.get_mode(modes[0])
                    if mc and getattr(mc, "provider", "") == prov_id:
                        current_model = getattr(mc, "model", "") or ""
        except Exception:  # noqa: BLE001
            logger.debug("Failed to read LLM mode router for model list", exc_info=True)

        total_pages = (len(models) + PAGE_SIZE - 1) // PAGE_SIZE
        page = max(0, min(page, total_pages - 1))
        start = page * PAGE_SIZE
        page_models = models[start : start + PAGE_SIZE]

        lines = [
            f"📝 <b>{tier_label} — {emoji} {html.escape(name)}</b>",
            "",
            f"Page {page + 1}/{total_pages} · {len(models)} models",
            "",
            "Tap a model to assign it:",
        ]

        keyboard: list = []
        for i, model in enumerate(page_models):
            idx = start + i
            short = model.split("/")[-1].split(":")[-1]
            is_current = model == current_model
            prefix = "✅ " if is_current else ""
            keyboard.append(
                [
                    {
                        "text": f"{prefix}{short}",
                        "callback_data": f"mdl_sel_{prov_id}_{idx}_{tier_code}_{page}",
                    }
                ]
            )

        # Pagination buttons
        nav_row: list = []
        if page > 0:
            nav_row.append(
                {"text": "◀ Prev", "callback_data": f"mdl_page_{prov_id}_{tier_code}_{page - 1}"}
            )
        if page < total_pages - 1:
            nav_row.append(
                {"text": "Next ▶", "callback_data": f"mdl_page_{prov_id}_{tier_code}_{page + 1}"}
            )
        if nav_row:
            keyboard.append(nav_row)

        keyboard.append([{"text": "← Back to tiers", "callback_data": f"mdl_back_tiers_{prov_id}"}])
        keyboard.append([{"text": "✖ Close", "callback_data": "mdl_close"}])

        text_payload = "\n".join(lines)
        if message_id:
            try:
                await self.edit_message(
                    chat_id,
                    message_id,
                    text_payload,
                    parse_mode="HTML",
                    keyboard=keyboard,
                )
                return
            except Exception:  # noqa: BLE001
                pass
        await self.send_message(chat_id, text_payload, parse_mode="HTML", keyboard=keyboard)

    async def _handle_ai_command(
        self,
        chat_id: int,
        user_id: int = 0,
        text: str = "",
        message_id: int | None = None,
    ) -> None:
        """/ai — Compact tier switcher.

        Shows current tier and lets the user switch (Small/Big/Coder/Auto)
        in one tap.  Provider/model details are in /providers and /models.
        """
        current_tier = ""
        if hasattr(self, "_get_user_tier_pref"):
            current_tier = self._get_user_tier_pref(chat_id, user_id)
        else:
            current_tier = (getattr(self, "_user_model_prefs", {}) or {}).get(user_id, "")

        # Quick model summary from LLM Mode Router
        model_summary = ""
        try:
            from navig.llm_router import get_llm_router

            lr = get_llm_router()
            if lr:
                big = lr.modes.get_mode("big_tasks")
                if big and getattr(big, "model", None):
                    short = (big.model or "").split("/")[-1].split(":")[-1]
                    prov = getattr(big, "provider", "") or ""
                    model_summary = f"\nActive: <code>{prov}:{short}</code>"
        except Exception:  # noqa: BLE001
            pass

        tier_label = {
            "": "auto",
            "small": "small",
            "big": "big",
            "coder_big": "coder",
            "noai": "no AI",
        }.get(current_tier, current_tier)

        lines = [
            "🤖 <b>AI Tier</b>",
            "",
            f"Current: <code>{tier_label}</code>{model_summary}",
            "",
            "Tap to switch:",
        ]

        # Build tier-picker keyboard (2 buttons per row)
        tier_rows: list = []
        tier_order = [
            ("small", "⚡ Small"),
            ("big", "🧠 Big"),
            ("coder_big", "💻 Coder"),
            ("", "🔄 Auto"),
        ]
        row: list = []
        for tier_key, btn_label in tier_order:
            check = " ✓" if current_tier == tier_key else ""
            row.append(
                {
                    "text": f"{btn_label}{check}",
                    "callback_data": f"aitier_{tier_key or 'auto'}",
                }
            )
            if len(row) == 2:
                tier_rows.append(row)
                row = []
        if row:
            tier_rows.append(row)

        tier_rows.append(
            [
                {"text": "🎛️ Providers", "callback_data": "nav:providers"},
                {"text": "📝 Models", "callback_data": "nav:models"},
            ]
        )
        tier_rows.append(
            [
                {"text": "✖ Close", "callback_data": "ai_close"},
            ]
        )

        payload = "\n".join(lines)
        if message_id:
            await self.edit_message(
                chat_id,
                message_id,
                payload,
                parse_mode="HTML",
                keyboard=tier_rows,
            )
        else:
            await self.send_message(chat_id, payload, parse_mode="HTML", keyboard=tier_rows)

    async def _handle_providers(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
        text: str = "",
    ) -> None:
        """AI Provider Hub — one-tap activation with inline edit-in-place.

        Tap a provider to immediately activate it with curated model defaults.
        The ✅ indicator shows the currently active provider.
        Includes session override banner, vision status, and action shortcuts.
        """
        # Quick-focus: /providers openai  --  /provider anthropic  etc.
        provider_arg = ""
        for prefix in ("/providers", "/provider"):
            if text.lower().startswith(prefix):
                provider_arg = text[len(prefix) :].strip().lower()
                break
        if provider_arg:
            await self.send_message(
                chat_id,
                f"<b>Provider: <code>{html.escape(provider_arg)}</code></b>\n\nUse /settings \u2192 Providers to configure connection details, or check <code>~/.navig/config.yaml</code> under <code>llm_router.modes</code>.",
                parse_mode="HTML",
            )
            return
        bridge_online, bridge_url = await self._probe_bridge_grid()

        active_prov = ""
        try:
            from navig.llm_router import get_llm_router

            lr = get_llm_router()
            if lr:
                m = lr.modes.get_mode("big_tasks")
                if m and getattr(m, "provider", None):
                    active_prov = m.provider
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        if not active_prov and bridge_online:
            active_prov = "bridge_copilot"

        try:
            providers = self._list_enabled_providers()
        except Exception:
            providers = []

        user_pref = ""
        if hasattr(self, "_get_user_tier_pref"):
            user_pref = self._get_user_tier_pref(chat_id, user_id)
        else:
            user_pref = getattr(self, "_user_model_prefs", {}).get(user_id, "")

        # ── Session overrides banner ────────────────────────────────────
        session_overrides: dict = {}
        try:
            from navig.gateway.channels.telegram_sessions import get_session_manager

            sm = get_session_manager()
            session_overrides = sm.get_all_session_overrides(chat_id, user_id)
        except Exception:  # noqa: BLE001
            pass

        # ── Header ──────────────────────────────────────────────────────
        lines: list[str] = ["🎛️ <b>AI Providers</b>", ""]

        # Session override banner
        if session_overrides:
            override_parts: list[str] = []
            _tier_prov_keys = [
                k for k in session_overrides if k.startswith("tier_") and k.endswith("_provider")
            ]
            if _tier_prov_keys:
                _tier_names = [
                    k.replace("tier_", "").replace("_provider", "") for k in _tier_prov_keys
                ]
                override_parts.append(f"tiers={','.join(_tier_names)}")
            if "vision_provider" in session_overrides:
                override_parts.append(f"vision={session_overrides['vision_provider']}")
            if override_parts:
                lines.append(f"🔶 Session: {', '.join(override_parts)}")

        # Active provider line
        active_name = ""
        if active_prov and active_prov != "bridge_copilot":
            try:
                _am = self._get_provider_info(active_prov)
                active_name = f"{_am.emoji} {_am.display_name}" if _am else active_prov
            except Exception:  # noqa: BLE001
                active_name = active_prov
            lines.append(f"✅ Current: <b>{html.escape(active_name)}</b>")
        elif active_prov == "bridge_copilot":
            lines.append(f"✅ Current: <b>{_ni('bolt')} Bridge</b>")

        # Bridge status (single line)
        bridge_active = active_prov == "bridge_copilot"
        if bridge_online or bridge_active:
            lines.append(_format_bridge_status(bridge_online, bridge_url))

        # Compact model routing summary
        try:
            from navig.llm_router import get_llm_router

            llm_router = get_llm_router()
            if llm_router:
                small = llm_router.modes.get_mode("small_talk")
                big = llm_router.modes.get_mode("big_tasks")
                code = llm_router.modes.get_mode("coding")

                def _short(mode_obj):
                    if not mode_obj:
                        return "—"
                    model = getattr(mode_obj, "model", "") or ""
                    return model.split("/")[-1] if model else "—"

                lines.append("")
                lines.append(f"⚡<code>{_short(small)}</code> · 🧠<code>{_short(big)}</code> · 💻<code>{_short(code)}</code>")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        # Vision status
        try:
            from navig.providers.discovery import resolve_vision_model

            vision_result = resolve_vision_model(session_overrides or None)
            if vision_result:
                v_prov, v_model, v_reason = vision_result
                v_short = v_model.split("/")[-1].split(":")[-1]
                lines.append(f"👁 Vision: <code>{html.escape(v_short)}</code> ({html.escape(v_reason)})")
        except Exception:  # noqa: BLE001
            pass

        if user_pref == "noai":
            lines.append("")
            lines.append("🚫 <b>Next message:</b> <code>raw / no AI</code> <i>(one-shot)</i>")

        lines.append("")
        lines.append("Tap a provider to activate it:")

        # ── Provider buttons ────────────────────────────────────────────
        keyboard_rows: list = []
        # Bridge is always shown regardless of probe result so users can see it
        # exists even when VS Code is closed.  Suffix reflects actual state.
        if bridge_active:
            bridge_suffix = " ✅"
        elif bridge_online:
            bridge_suffix = ""
        else:
            bridge_suffix = " ⏸ offline"
        bridge_button = [
            {
                "text": f"{_ni('bolt')} Bridge{bridge_suffix}",
                "callback_data": "prov_bridge"
                if (bridge_online or bridge_active)
                else "prov_bridge_offline",
            }
        ]
        bridge_inserted = False
        ready_provider_count = 0

        for manifest in providers:
            if manifest.id == "mcp_bridge":
                continue
            vault_has_key = False
            key_detected = False
            result = None
            try:
                result = self._verify_provider(manifest)
                key_detected = bool(getattr(result, "key_detected", False))
                if manifest.tier == "local" and manifest.local_probe:
                    ready = bool(result.local_probe_ok)
                elif manifest.tier == "cloud" and manifest.requires_key:
                    try:
                        vault_has_key, vault_validated = self._provider_vault_validation_status(
                            manifest
                        )
                    except Exception:  # noqa: BLE001
                        vault_has_key = False
                    ready = key_detected or vault_has_key
                elif manifest.requires_key:
                    ready = key_detected
                else:
                    ready = True
            except Exception:
                ready = False

            if not ready:
                if (
                    getattr(manifest, "tier", "") == "cloud"
                    and getattr(manifest, "requires_key", False)
                    and not vault_has_key
                ):
                    # 🔒 = locked / needs API key setup (🔑 looks like "has a key")
                    stub_btn = {
                        "text": f"{manifest.emoji} {manifest.display_name} 🔒",
                        "callback_data": f"prov_cfg_{manifest.id}",
                    }
                    keyboard_rows.append([stub_btn])
                elif getattr(manifest, "tier", "") == "local":
                    # Show offline local providers so users know they exist.
                    stub_btn = {
                        "text": f"{manifest.emoji} {manifest.display_name} ⏸ offline",
                        "callback_data": f"prov_cfg_{manifest.id}",
                    }
                    keyboard_rows.append([stub_btn])
                    # Place Bridge after Ollama even when Ollama is offline
                    if manifest.id == "ollama" and not bridge_inserted:
                        keyboard_rows.append(bridge_button)
                        bridge_inserted = True
                continue

            ready_provider_count += 1
            is_active = manifest.id == active_prov
            active_suffix = " ✅" if is_active else ""
            btn = {
                "text": f"{manifest.emoji} {manifest.display_name}{active_suffix}",
                "callback_data": f"prov_{manifest.id}",
            }
            keyboard_rows.append([btn])

            # Place Bridge right after Ollama (online path)
            if manifest.id == "ollama" and not bridge_inserted:
                keyboard_rows.append(bridge_button)
                bridge_inserted = True

        # Fallback: Bridge wasn't placed after Ollama (Ollama absent from registry)
        if not bridge_inserted:
            keyboard_rows.append(bridge_button)

        if ready_provider_count == 0:
            lines.append("")
            lines.append("ℹ️ No vault-backed cloud providers are ready.")

        noai_prefix = "✅ " if user_pref == "noai" else ""
        keyboard_rows.append(
            [{"text": f"{noai_prefix}🚫 No AI — raw mode", "callback_data": "prov_noai"}]
        )

        # ── Action shortcuts ────────────────────────────────────────────
        keyboard_rows.append(
            [
                {"text": "🔀 Hybrid", "callback_data": "pu_hybrid"},
                {"text": "👁 Vision", "callback_data": "pu_vision"},
                {"text": "🎙 Voice",  "callback_data": "pu_voice"},
            ]
        )
        if session_overrides:
            keyboard_rows.append(
                [{"text": "🔄 Reset session", "callback_data": "pu_reset_session"}]
            )
        if message_id:
            keyboard_rows.append(
                [
                    {"text": "🔙 Back", "callback_data": "nav:back"},
                    {"text": "🏠 Home", "callback_data": "nav:home"},
                ]
            )
        keyboard_rows.append([{"text": "✖ Close", "callback_data": "prov_close"}])

        text_payload = "\n".join(lines)
        if message_id:
            try:
                await self.edit_message(
                    chat_id,
                    message_id,
                    text_payload,
                    parse_mode="HTML",
                    keyboard=keyboard_rows,
                )
                return
            except Exception:  # noqa: BLE001
                pass  # fall through to fresh send

        await self.send_message(
            chat_id, text_payload, parse_mode="HTML", keyboard=keyboard_rows
        )

    async def _show_provider_activation_confirmation(
        self,
        chat_id: int,
        prov_id: str,
        defaults: dict[str, str],
        message_id: int | None = None,
    ) -> None:
        """Show inline confirmation after one-tap provider activation."""
        emoji, name = "", prov_id
        try:
            from navig.providers.registry import get_provider as _gp

            manifest = _gp(prov_id)
            if manifest:
                emoji = manifest.emoji
                name = manifest.display_name
        except Exception:  # noqa: BLE001
            pass  # best-effort

        def _short(m: str) -> str:
            return m.split("/")[-1].split(":")[-1] if m else "—"

        lines = [
            f"✅ <b>{emoji} {html.escape(name)} activated!</b>",
            "",
            f"⚡ Small: <code>{_short(defaults.get('small', ''))}</code>",
            f"🧠 Big: <code>{_short(defaults.get('big', ''))}</code>",
            f"💻 Code: <code>{_short(defaults.get('coder_big', ''))}</code>",
            "",
            "<i>Saved to config. Use /models to customise per tier.</i>",
        ]

        keyboard = [
            [
                {"text": "📝 Customize models", "callback_data": f"prov_customize_{prov_id}"},
                {"text": "← Providers", "callback_data": "prov_back"},
            ],
            [{"text": "✖ Close", "callback_data": "prov_close"}],
        ]

        text_payload = "\n".join(lines)
        if message_id:
            try:
                await self.edit_message(
                    chat_id,
                    message_id,
                    text_payload,
                    parse_mode="HTML",
                    keyboard=keyboard,
                )
                return
            except Exception:  # noqa: BLE001
                pass  # fall through to send_message
        await self.send_message(chat_id, text_payload, parse_mode="HTML", keyboard=keyboard)

    # ── Dependency seams ── override in a subclass to inject test doubles ──────

    def _get_vault(self):
        """Return the active vault instance.  Override in tests via subclass."""
        from navig.vault import get_vault

        return get_vault()

    def _get_provider_info(self, provider_id: str):
        """Return the provider manifest for *provider_id*, or ``None``."""
        try:
            from navig.providers.registry import get_provider

            return get_provider(provider_id)
        except Exception:  # noqa: BLE001
            return None

    def _list_enabled_providers(self) -> list:
        """Return all enabled provider manifests.  Override in tests."""
        from navig.providers.registry import list_enabled_providers

        return list_enabled_providers()

    def _verify_provider(self, manifest):
        """Verify a single provider manifest.  Override in tests."""
        from navig.providers.verifier import verify_provider

        return verify_provider(manifest)

    def _provider_vault_validation_status(self, manifest) -> tuple[bool, bool]:
        """Return ``(has_vault_key, is_validated)`` for a cloud provider manifest."""
        has_vault_key = False
        validated = False

        try:
            vault = self._get_vault()
            if vault is not None:
                # Primary: canonical label (e.g. "openai") via get_api_key
                try:
                    key = vault.get_api_key(manifest.id)
                    if key:
                        has_vault_key = True
                except Exception:  # noqa: BLE001
                    pass

                # Primary fallback: decryption-free presence check for canonical label.
                # vault.store().get() is pure SQL — safe even when CryptoEngine fails in daemon.
                if not has_vault_key:
                    try:
                        _store = vault.store()
                        if _store.get(manifest.id) is not None:
                            has_vault_key = True
                    except Exception:  # noqa: BLE001
                        pass

                # Secondary: manifest-declared vault_keys (e.g. "openai/api-key")
                if not has_vault_key:
                    store = vault.store()
                    for label in getattr(manifest, "vault_keys", []) or []:
                        try:
                            item = store.get(label)
                        except Exception:
                            continue
                        if item is None:
                            continue
                        has_vault_key = True
                        try:
                            metadata = getattr(item, "metadata", {}) if item else {}
                            if (
                                isinstance(metadata, dict)
                                and metadata.get("validation_success") is True
                            ):
                                validated = True
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("Vault metadata check failed for %s: %s", label, exc)
                        break
                # Tertiary: env_var names as vault labels (covers legacy/alternative
                # storage — e.g. GITHUB_TOKEN stored as 'github_token')
                if not has_vault_key:
                    _already_tried = {manifest.id} | set(getattr(manifest, "vault_keys", []) or [])
                    for var in getattr(manifest, "env_vars", []) or []:
                        var_lower = var.lower()
                        if var_lower in _already_tried:
                            continue
                        try:
                            key = vault.get_api_key(var_lower)
                            if key:
                                has_vault_key = True
                                break
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            item = store.get(var_lower)
                            if item is not None:
                                has_vault_key = True
                                break
                        except Exception:  # noqa: BLE001
                            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("Vault provider readiness check failed: %s", exc)

        return has_vault_key, validated

    async def _resolve_provider_models(self, prov_id: str, manifest=None) -> list[str]:
        """Resolve models for provider using live endpoints when available."""
        models: list[str] = []
        if manifest is None:
            try:
                from navig.providers.registry import _INDEX as PROV_INDEX

                manifest = PROV_INDEX.get(prov_id)
            except Exception:
                manifest = None
        if manifest and getattr(manifest, "models", None):
            models = list(manifest.models)

        if prov_id == "ollama":
            try:
                import aiohttp

                async with (
                    aiohttp.ClientSession() as session,
                    session.get("http://127.0.0.1:11434/api/tags", timeout=2) as response,
                ):
                    data = await response.json()
                    live = [m["name"] for m in data.get("models", []) if m.get("name")]
                    if live:
                        models = live
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not fetch live Ollama models: %s", exc)
            if not models:
                models = ["qwen2.5:7b", "qwen2.5:3b", "phi3.5", "llama3.2"]
            return models

        if prov_id == "llamacpp":
            try:
                import aiohttp

                async with (
                    aiohttp.ClientSession() as session,
                    session.get("http://127.0.0.1:8080/v1/models", timeout=2) as response,
                ):
                    data = await response.json()
                    live = [m["id"] for m in data.get("data", []) if m.get("id")]
                    if live:
                        models = live
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not fetch live llama.cpp models: %s", exc)
            if not models:
                models = ["llama.cpp/default", "llama3.2", "qwen2.5:7b"]
            return models

        try:
            import os

            import aiohttp

            endpoint = ""
            headers = {}
            def _get_key(pid: str) -> str:
                """Resolve API key for provider from env, then vault/config."""
                env_map = {
                    "openrouter": ["OPENROUTER_API_KEY"],
                    "xai": ["XAI_API_KEY", "GROK_KEY"],
                    "nvidia": ["NVIDIA_API_KEY", "NIM_API_KEY"],
                }
                for _env in env_map.get(pid, []):
                    _v = os.environ.get(_env, "")
                    if _v:
                        return _v
                try:
                    from navig.agent.model_router import _resolve_provider_api_key

                    return _resolve_provider_api_key(pid) or ""
                except Exception:  # noqa: BLE001
                    return ""

            if prov_id == "openrouter":
                api_key = _get_key("openrouter")
                if api_key:
                    endpoint = "https://openrouter.ai/api/v1/models"
                    headers = {"Authorization": f"Bearer {api_key}"}
            elif prov_id == "xai":
                api_key = _get_key("xai")
                if api_key:
                    endpoint = "https://api.x.ai/v1/models"
                    headers = {"Authorization": f"Bearer {api_key}"}
            elif prov_id == "nvidia":
                api_key = _get_key("nvidia")
                if api_key:
                    endpoint = "https://integrate.api.nvidia.com/v1/models"
                    headers = {"Authorization": f"Bearer {api_key}"}

            if endpoint:
                async with (
                    aiohttp.ClientSession() as session,
                    session.get(endpoint, headers=headers, timeout=_PROBE_TIMEOUT) as response,
                ):
                    data = await response.json()
                    live = [m["id"] for m in data.get("data", []) if m.get("id")]
                    if live:
                        models = live
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not fetch live model list for %s: %s", prov_id, exc)

        return models

    @staticmethod
    def _select_curated_tier_defaults(prov_id: str, models: list[str]) -> dict[str, str]:
        """Select curated defaults for small/big/coder_big tiers."""
        if not models:
            return {"small": "", "big": "", "coder_big": ""}

        lowered = [(m, m.lower()) for m in models]

        def _pick(preferred: tuple[str, ...], fallback: str = "") -> str:
            for raw, low in lowered:
                if any(token in low for token in preferred):
                    return raw
            return fallback or models[0]

        if prov_id == "openai":
            big = _pick(("gpt-4o",), models[0])
            # prefer mini for small, but not the big gpt-4o itself
            small = _pick(("gpt-4o-mini", "gpt-3.5"), big)
            coder = big
            return {"small": small, "big": big, "coder_big": coder}

        if prov_id == "anthropic":
            big = _pick(("sonnet",), models[0])
            small = _pick(("haiku",), models[-1])
            coder = big
            return {"small": small, "big": big, "coder_big": coder}

        if prov_id == "google":
            big = _pick(("pro",), models[0])
            small = _pick(("flash",), models[-1])
            coder = small  # flash is faster for code
            return {"small": small, "big": big, "coder_big": coder}

        if prov_id == "groq":
            big = _pick(("70b",), models[0])
            small = _pick(("mixtral", "8x7b", "8b", "7b"), models[-1])
            coder = big
            return {"small": small, "big": big, "coder_big": coder}

        if prov_id == "nvidia":
            # Vision/multimodal models cannot handle plain text chat — exclude them
            # from the text-chat slots to prevent empty responses and fallback to
            # the reasoning model (dracarys) which leaks <think> chains to users.
            _VISION_TOKENS = ("fuyu", "clip", "llava", "blip", "visual", "flamingo", "neva")

            def _pick_text(preferred: tuple[str, ...], fallback: str = "") -> str:
                """Like _pick but excludes vision/multimodal models."""
                text_models = [(r, l) for r, l in lowered if not any(vt in l for vt in _VISION_TOKENS)]
                for raw, low in text_models:
                    if any(token in low for token in preferred):
                        return raw
                # Fallback: first non-vision model, or fallback arg, or first model overall
                return text_models[0][0] if text_models else (fallback or models[0])

            big = _pick(("70b",), models[0])
            small = _pick_text(("8b", "7b"), models[-1])
            coder = _pick(("deepseek",), big)
            return {"small": small, "big": big, "coder_big": coder}

        if prov_id == "github_models":
            big = _pick(("405b", "llama"), models[0])
            small = _pick(("mini", "phi"), models[-1])
            coder = _pick(("gpt-4o",), big)
            return {"small": small, "big": big, "coder_big": coder}

        if prov_id == "xai":
            big = _pick(("grok-3", "grok-2"), models[0])
            small = _pick(("mini",), models[-1])
            coder = _pick(("code", "coder"), big)
            return {"small": small, "big": big, "coder_big": coder}

        big = _pick(("405b", "120b", "90b", "72b", "70b", "67b", "34b", "32b"), models[0])
        small = _pick(("mini", "8b", "7b", "3b", "2b", "1b"), models[-1])
        coder = _pick(("coder", "code", "deepseek-coder"), big)
        return {"small": small, "big": big, "coder_big": coder}

    def _persist_hybrid_router_assignments(self, router_cfg) -> None:
        """Persist current hybrid router slots to global config."""
        from navig.config import get_config_manager

        cfg_mgr = get_config_manager()
        global_cfg = dict(cfg_mgr.global_config or {})
        ai_cfg = dict(global_cfg.get("ai") or {})
        routing_cfg = dict(ai_cfg.get("routing") or {})

        routing_cfg["enabled"] = True
        if not routing_cfg.get("mode"):
            routing_cfg["mode"] = "rules_then_fallback"

        routing_cfg["models"] = {
            "small": {
                "provider": router_cfg.small.provider,
                "model": router_cfg.small.model,
                "defaults": {
                    "max_tokens": router_cfg.small.max_tokens,
                    "temperature": router_cfg.small.temperature,
                    "num_ctx": router_cfg.small.num_ctx,
                },
            },
            "big": {
                "provider": router_cfg.big.provider,
                "model": router_cfg.big.model,
                "defaults": {
                    "max_tokens": router_cfg.big.max_tokens,
                    "temperature": router_cfg.big.temperature,
                    "num_ctx": router_cfg.big.num_ctx,
                },
            },
            "coder_big": {
                "provider": router_cfg.coder_big.provider,
                "model": router_cfg.coder_big.model,
                "defaults": {
                    "max_tokens": router_cfg.coder_big.max_tokens,
                    "temperature": router_cfg.coder_big.temperature,
                    "num_ctx": router_cfg.coder_big.num_ctx,
                },
            },
        }
        ai_cfg["routing"] = routing_cfg
        cfg_mgr.update_global_config({"ai": ai_cfg})

    # ── Tier → LLM-mode mapping ─────────────────────────────────────────
    _TIER_TO_MODES: dict[str, list[str]] = {
        "small": ["small_talk"],
        "big": ["big_tasks"],
        "coder_big": ["coding"],
    }

    def _refresh_ai_runtime_after_router_update(self) -> None:
        """Best-effort refresh of router/client singletons after provider/model updates."""
        try:
            from navig.routing.router import reset_router

            reset_router()
        except Exception:  # noqa: BLE001
            logger.debug("Failed to reset unified router singleton", exc_info=True)
        # Flush ConversationalAgent cache so next message gets a fresh agent
        # with the updated provider/model resolved through UnifiedRouter.
        try:
            flush_fn = getattr(self, "flush_conv_agents", None)
            if flush_fn is None:
                # Try via gateway reference (when self is not a ChannelRouter subclass)
                gw = getattr(self, "gateway", None)
                if gw is not None:
                    flush_fn = getattr(getattr(gw, "channel_router", None), "flush_conv_agents", None)
            if callable(flush_fn):
                flush_fn()
        except Exception:  # noqa: BLE001
            logger.debug("Failed to flush ConversationalAgent cache", exc_info=True)

        try:
            from navig.llm_router import get_llm_router

            get_llm_router(force_new=True)
        except Exception:  # noqa: BLE001
            logger.debug("Failed to refresh llm router singleton", exc_info=True)

        try:
            from navig.agent.ai_client import get_ai_client

            client = get_ai_client()
            if hasattr(client, "_init_model_router"):
                client._init_model_router()
            if hasattr(client, "re_detect_provider"):
                client.re_detect_provider()
        except Exception:  # noqa: BLE001
            logger.debug("Failed to refresh ai client routing/provider state", exc_info=True)

    def _update_llm_mode_router(self, provider_id: str, tier_models: dict[str, str]) -> None:
        """Update the LLM Mode Router for the given tiers and persist to config.

        Parameters
        ----------
        provider_id:
            The canonical provider id (e.g. ``"openai"``).
        tier_models:
            Mapping of tier → model name, e.g.
            ``{"small": "gpt-4o-mini", "big": "gpt-4o", "coder_big": "gpt-4o"}``.
        """
        try:
            from navig.config import get_config_manager
            from navig.llm_router import get_llm_router

            router = get_llm_router()
            if not router:
                return

            for tier, model in tier_models.items():
                # Use class reference (self may be TelegramChannel, not mixin)
                for mode_name in TelegramCommandsMixin._TIER_TO_MODES.get(tier, []):
                    router.update_mode(mode_name, provider=provider_id, model=model)

            # Persist both llm_router.llm_modes and ai.default_provider in a single
            # call to avoid two-snapshot race conditions and double disk writes.
            all_modes = router.get_all_modes()
            cfg_mgr = get_config_manager()
            # Re-read from live config (not a stale local snapshot) for both keys.
            live_cfg = cfg_mgr.global_config or {}
            llm_router_cfg = dict(live_cfg.get("llm_router") or {})
            llm_router_cfg["llm_modes"] = all_modes
            ai_cfg = dict(live_cfg.get("ai") or {})
            ai_cfg["default_provider"] = provider_id
            cfg_mgr.update_global_config({"llm_router": llm_router_cfg, "ai": ai_cfg})
            self._refresh_ai_runtime_after_router_update()
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to persist LLM mode router assignments for %s",
                provider_id,
                exc_info=True,
            )

    def _deactivate_provider(self, provider_id: str) -> None:
        """Clear all LLM mode router slots pointing at ``provider_id`` and persist.

        After this call every mode that was using the provider is reset to an
        empty provider/model so the router falls back to raw (no-AI) operation
        until a new provider is activated.
        """
        try:
            from navig.config import get_config_manager
            from navig.llm_router import get_llm_router

            router = get_llm_router()
            if not router:
                return

            all_modes = router.get_all_modes()
            for mode_name, mode_cfg in all_modes.items():
                if isinstance(mode_cfg, dict) and mode_cfg.get("provider") == provider_id:
                    router.update_mode(mode_name, provider="", model="")

            # Persist cleared state
            updated_modes = router.get_all_modes()
            cfg_mgr = get_config_manager()
            global_cfg = dict(cfg_mgr.global_config or {})
            llm_router_cfg = dict(global_cfg.get("llm_router") or {})
            llm_router_cfg["llm_modes"] = updated_modes
            cfg_mgr.update_global_config({"llm_router": llm_router_cfg})
            self._refresh_ai_runtime_after_router_update()
        except Exception:  # noqa: BLE001
            logger.debug(
                "Failed to deactivate provider %s from LLM mode router",
                provider_id,
                exc_info=True,
            )

    @error_handled
    @typing_context
    async def _show_provider_model_picker(
        self,
        chat_id: int,
        prov_id: str,
        page: int = 0,
        selected_tier: str = "s",
        message_id: int | None = None,
        show_models: bool = False,
    ) -> None:
        """Show tier-first model picker for a provider with edit-in-place pagination."""
        emoji, name = _ni("robot"), prov_id
        models: list[str] = []
        try:
            from navig.providers.registry import _INDEX as PROV_INDEX

            manifest = PROV_INDEX.get(prov_id)
            if manifest:
                emoji = manifest.emoji
                name = manifest.display_name
        except Exception:
            manifest = None

        models = await self._resolve_provider_models(prov_id, manifest=manifest)

        if not models:
            await self.send_message(
                chat_id, f"{_ni('warn')} No models found for {prov_id}.", parse_mode=None
            )
            return

        # Check API key status for banner display
        _key_missing = False
        _key_hint = ""
        if manifest and getattr(manifest, "requires_key", False):
            try:
                from navig.providers.verifier import verify_provider as _vp

                _vr = _vp(manifest)
                if not getattr(_vr, "key_detected", False):
                    vault_ok, _ = self._provider_vault_validation_status(manifest)
                    if not vault_ok:
                        _key_missing = True
                        env_hint = " / ".join(manifest.env_vars[:2]) if manifest.env_vars else ""
                        vault_hint = manifest.vault_keys[0] if manifest.vault_keys else ""
                        parts = [
                            p
                            for p in [env_hint, f"vault '{vault_hint}'" if vault_hint else ""]
                            if p
                        ]
                        _key_hint = " or ".join(parts) if parts else "provider config"
            except Exception:  # noqa: BLE001
                pass  # best-effort; display continues without banner

        # Resolve currently-assigned models for each tier
        # Priority 1: hybrid router (when active and assigned to this provider)
        current: dict = {"small": None, "big": None, "coder_big": None}
        try:
            from navig.agent.ai_client import get_ai_client

            router = get_ai_client().model_router
            if router and router.is_active:
                for tier in ("small", "big", "coder_big"):
                    slot = router.cfg.slot_for_tier(tier)
                    if slot.provider == prov_id and slot.model:
                        current[tier] = slot.model
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        # Priority 2: LLM Mode Router (written on every activation, even without hybrid)
        if any(v is None for v in current.values()):
            try:
                from navig.llm_router import get_llm_router

                lr = get_llm_router()
                if lr:
                    for tier, modes in TelegramCommandsMixin._TIER_TO_MODES.items():
                        if current[tier] is not None:
                            continue  # already filled by hybrid router
                        mc = lr.modes.get_mode(modes[0])
                        if mc and getattr(mc, "model", None) and getattr(mc, "provider", "") == prov_id:
                            current[tier] = mc.model
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        # Priority 3: curated defaults — always shown for ALL providers so users see what
        # models would be assigned even before explicitly activating the provider.
        if any(v is None for v in current.values()):
            try:
                _curated = TelegramCommandsMixin._select_curated_tier_defaults(prov_id, models)
                for tier in ("small", "big", "coder_big"):
                    if current[tier] is None and _curated.get(tier):
                        current[tier] = _curated[tier]
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        # Build tier display labels using HTML (safe for all model name chars)
        tier_emoji = {"small": _ni("bolt"), "big": _ni("brain"), "coder_big": _ni("computer")}
        tier_label = {"small": "Small", "big": "Big", "coder_big": "Code"}

        def _tier_line(tier: str) -> str:
            val = current[tier]
            if val:
                short_val = val.split("/")[-1].split(":")[-1]
                return f"{tier_emoji[tier]} {tier_label[tier]}: <code>{short_val}</code>"
            return f"{tier_emoji[tier]} {tier_label[tier]}: —"

        selected_tier = selected_tier if selected_tier in {"s", "b", "c"} else "s"
        tier_code_to_name = {"s": "small", "b": "big", "c": "coder_big"}
        tier_name = tier_code_to_name[selected_tier]

        _PAGE_SIZE = 20
        page_size = _PAGE_SIZE
        total_pages = max(1, (len(models) + page_size - 1) // page_size)
        page = min(max(0, page), total_pages - 1)
        start = page * page_size
        end = start + page_size
        page_models = models[start:end]

        # Pagination header shown in text only when there are multiple pages
        _viewing_header = f"{tier_emoji[tier_name]} {tier_label[tier_name]}"
        if total_pages > 1:
            _viewing_header += f" ({page + 1}/{total_pages})"
        lines = [
            f"<b>{emoji} {html.escape(name)}</b> — assign model to tier",
            "",
        ]
        if _key_missing:
            lines.append(f"⚠️ <i>API key not configured — set {_key_hint}</i>")
            lines.append("")
        _action_hint = (
            f"<i>Assigning to: {_viewing_header} — tap a model below.</i>"
            if show_models
            else "<i>Tap Small, Big, or Code to pick a model.</i>"
        )
        lines.extend(
            [
                _tier_line("small"),
                _tier_line("big"),
                _tier_line("coder_big"),
                "",
                _action_hint,
            ]
        )
        # Model list shown only as keyboard buttons (no duplicate numbered text list)

        keyboard: list[list[dict[str, str]]] = []
        keyboard.append(
            [
                {
                    "text": f"{_ni('bolt')} Small",
                    "callback_data": f"pmv_{prov_id}_s_{page}",
                },
                {
                    "text": f"{_ni('brain')} Big",
                    "callback_data": f"pmv_{prov_id}_b_{page}",
                },
                {
                    "text": f"{_ni('computer')} Code",
                    "callback_data": f"pmv_{prov_id}_c_{page}",
                },
            ]
        )
        if show_models:
            for offset, m in enumerate(page_models):
                idx = start + offset
                short = m.split("/")[-1].split(":")[-1]
                label = short if len(short) <= 42 else short[:39] + "..."
                keyboard.append(
                    [
                        {
                            "text": f"{label}",
                            "callback_data": f"pms_{prov_id}_{idx}_{selected_tier}_{page}",
                        }
                    ]
                )

        if show_models and total_pages > 1:
            page_nav: list[dict[str, str]] = []
            if page > 0:
                page_nav.append(
                    {
                        "text": "⬅️ Prev",
                        "callback_data": f"prov_page_{prov_id}_{selected_tier}_{page - 1}",
                    }
                )
            page_nav.append({"text": f"📄 {page + 1}/{total_pages}", "callback_data": "prov_noop"})
            if page < total_pages - 1:
                page_nav.append(
                    {
                        "text": "Next ➡️",
                        "callback_data": f"prov_page_{prov_id}_{selected_tier}_{page + 1}",
                    }
                )
            keyboard.append(page_nav)

        # Show Activate / Deactivate button depending on live router state
        _is_active_prov = False
        try:
            from navig.llm_router import get_llm_router as _glr

            _lr = _glr()
            if _lr:
                _is_active_prov = any(
                    isinstance(v, dict) and v.get("provider") == prov_id
                    for v in _lr.get_all_modes().values()
                )
        except Exception:  # noqa: BLE001
            pass
        if _is_active_prov:
            keyboard.append(
                [{"text": f"🔴 Deactivate {name}", "callback_data": f"prov_deactivate_{prov_id}"}]
            )
        else:
            keyboard.append(
                [{"text": f"🚀 Activate {name}", "callback_data": f"prov_activate_{prov_id}"}]
            )
        keyboard.append(
            [
                {"text": "🔙 Back", "callback_data": "nav:providers"},
                {"text": "🏠 Home", "callback_data": "nav:home"},
            ]
        )
        keyboard.append([{"text": "✖ Close", "callback_data": "prov_close"}])

        text_payload = "\n".join(lines)
        if message_id:
            try:
                result = await self.edit_message(
                    chat_id,
                    message_id,
                    text_payload,
                    parse_mode="HTML",
                    keyboard=keyboard,
                )
                # _api_call never raises — check return value to detect silent failures
                if result is not None:
                    return
                logger.debug(
                    "Provider picker edit_message returned None for %s in chat %s (message %s). Falling back to send_message.",
                    prov_id,
                    chat_id,
                    message_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Provider picker edit_message raised for %s in chat %s (message %s): %s. Falling back to send_message.",
                    prov_id,
                    chat_id,
                    message_id,
                    exc,
                )

        await self.send_message(chat_id, text_payload, keyboard=keyboard, parse_mode="HTML")

    # ── Provider control surface: handler methods ────────────────────────

    async def _handle_provider_hybrid(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
        text: str = "",
    ) -> None:
        """Hybrid routing — one-tap per-tier provider/model assignment.

        Session-only by default.  A "Save as default" button persists to config.
        """
        try:
            from navig.providers.discovery import (
                _get_current_tier_assignments,
                list_connected_providers,
            )
        except Exception:  # noqa: BLE001
            await self.send_message(chat_id, "⚠️ Provider discovery not available.", parse_mode=None)
            return

        providers = list_connected_providers()
        tier_assignments = _get_current_tier_assignments()

        # ── Session overrides ───────────────────────────────────────────
        session_overrides: dict = {}
        try:
            from navig.gateway.channels.telegram_sessions import get_session_manager

            sm = get_session_manager()
            session_overrides = sm.get_all_session_overrides(chat_id, user_id)
        except Exception:  # noqa: BLE001
            pass

        tier_emoji = {"small": "⚡", "big": "🧠", "coder_big": "💻"}
        tier_label = {"small": "Small", "big": "Big", "coder_big": "Code"}

        # Check if hybrid router is active in the current process
        _hybrid_active = False
        try:
            from navig.agent.ai_client import get_ai_client

            _hr = get_ai_client().model_router
            if _hr and _hr.is_active:
                _hybrid_active = True
        except Exception:  # noqa: BLE001
            pass

        lines: list[str] = ["<b>🔀 Hybrid Routing</b>", ""]
        if not _hybrid_active:
            lines.append("⚠️ <i>Hybrid routing is currently disabled.</i>")
            lines.append("")
        lines.append("Assign a provider to each tier. Tap a tier to change it.")
        lines.append("")

        for tier in ("small", "big", "coder_big"):
            # Session override takes precedence for display
            so_prov = session_overrides.get(f"tier_{tier}_provider", "")
            so_model = session_overrides.get(f"tier_{tier}_model", "")
            if so_prov and so_model:
                short_model = so_model.split("/")[-1].split(":")[-1]
                lines.append(
                    f"{tier_emoji[tier]} {tier_label[tier]}: "
                    f"<code>{short_model}</code> ({so_prov}) 🔶"
                )
            elif tier in tier_assignments:
                prov_id, model = tier_assignments[tier]
                short_model = model.split("/")[-1].split(":")[-1] if model else "—"
                prov_display = prov_id or "—"
                lines.append(
                    f"{tier_emoji[tier]} {tier_label[tier]}: "
                    f"<code>{short_model}</code> ({prov_display})"
                )
            else:
                lines.append(f"{tier_emoji[tier]} {tier_label[tier]}: —")

        if session_overrides:
            lines.append("")
            lines.append("🔶 = session override (not saved to config)")

        lines.append("")
        lines.append("<i>Tap a tier below to pick a provider for it.</i>")

        # Build keyboard: one row per tier
        keyboard: list[list[dict[str, str]]] = []
        for tier in ("small", "big", "coder_big"):
            keyboard.append(
                [
                    {
                        "text": f"{tier_emoji[tier]} {tier_label[tier]}",
                        "callback_data": f"hyb_tier_{tier}",
                    }
                ]
            )

        # Connected providers summary row (for quick pick).
        # Exclude AirLLM — it probes as always-connected but is a local inference
        # runner, not a cloud tier target for hybrid routing.
        connected = [p for p in providers if p.connected and p.id != "airllm"]
        if connected:
            prov_row: list[dict[str, str]] = []
            for p in connected[:4]:  # max 4 per row
                prov_row.append(
                    {"text": f"{p.emoji} {p.display_name}", "callback_data": f"hyb_pick_{p.id}"}
                )
            keyboard.append(prov_row)

        # Enable hybrid routing button (only when not active)
        if not _hybrid_active:
            keyboard.append(
                [{"text": "🔛 Enable Hybrid Routing", "callback_data": "hyb_enable"}]
            )

        # Save / Reset / Back
        if session_overrides:
            keyboard.append(
                [
                    {"text": "💾 Save as default", "callback_data": "hyb_save"},
                    {"text": "🔄 Reset session", "callback_data": "hyb_reset"},
                ]
            )
        keyboard.append(
            [
                {"text": "🔙 Back", "callback_data": "nav:back"},
                {"text": "✖ Close", "callback_data": "prov_close"},
            ]
        )

        text_payload = "\n".join(lines)
        if message_id:
            if await self.edit_message(
                chat_id, message_id, text_payload, parse_mode="HTML", keyboard=keyboard
            ) is not None:
                return
        await self.send_message(chat_id, text_payload, parse_mode="HTML", keyboard=keyboard)

    async def _handle_provider_vision(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
        text: str = "",
    ) -> None:
        """Vision model picker — select which model handles image analysis."""
        try:
            from navig.providers.capabilities import Capability
            from navig.providers.discovery import list_available_models, resolve_vision_model
        except Exception:  # noqa: BLE001
            await self.send_message(chat_id, "⚠️ Provider discovery not available.", parse_mode=None)
            return

        # Get session overrides for current resolution
        session_overrides: dict = {}
        try:
            from navig.gateway.channels.telegram_sessions import get_session_manager

            sm = get_session_manager()
            session_overrides = sm.get_all_session_overrides(chat_id, user_id)
        except Exception:  # noqa: BLE001
            pass

        vision_models = list_available_models(capability=Capability.VISION, connected_only=True)
        current = resolve_vision_model(session_overrides)

        lines: list[str] = ["<b>👁 Vision Model</b>", ""]

        if current:
            cur_prov, cur_model, cur_reason = current
            short_model = cur_model.split("/")[-1].split(":")[-1]
            reason_labels = {
                "session_override": "session override 🔶",
                "global_config": "saved in config",
                "active_provider": "from active provider",
                "fallback": "auto-detected fallback",
            }
            reason_text = reason_labels.get(cur_reason, cur_reason)
            lines.append(
                f"Active: <code>{html.escape(short_model)}</code> ({html.escape(cur_prov)})"
            )
            lines.append(f"Source: {reason_text}")
        else:
            lines.append("⚠️ No vision model available — connect a vision-capable provider.")

        lines.append("")

        if vision_models:
            lines.append("<i>Tap a model to use it for image analysis:</i>")
            lines.append("")
            # Group by provider
            by_provider: dict[str, list] = {}
            for m in vision_models:
                by_provider.setdefault(m.provider_id, []).append(m)

            for prov_id, models in by_provider.items():
                for m in models[:5]:  # limit per provider
                    short = m.name.split("/")[-1].split(":")[-1]
                    is_cur = current and current[0] == m.provider_id and current[1] == m.name
                    marker = " ✅" if is_cur else ""
                    lines.append(
                        f"  • {html.escape(m.capability_label)} <code>{html.escape(short)}</code> ({html.escape(prov_id)}){marker}"
                    )
        else:
            lines.append("No vision-capable models found from connected providers.")

        # Build keyboard
        keyboard: list[list[dict[str, str]]] = []

        def _safe_vis_callback_data(prov_id: str, model_name: str) -> str | None:
            prefix = f"vis_{prov_id}:"
            max_payload_bytes = 64 - len(prefix.encode("utf-8"))
            if max_payload_bytes <= 0:
                return None
            token = model_name
            while token and len(token.encode("utf-8")) > max_payload_bytes:
                token = token[:-1]
            if not token:
                return None
            return f"{prefix}{token}"

        for m in vision_models[:12]:  # max 12 buttons
            short = m.name.split("/")[-1].split(":")[-1]
            is_cur = current and current[0] == m.provider_id and current[1] == m.name
            check = " ✅" if is_cur else ""
            callback_data = _safe_vis_callback_data(m.provider_id, m.name)
            if not callback_data:
                continue
            keyboard.append(
                [
                    {
                        "text": f"👁 {short}{check}",
                        "callback_data": callback_data,
                    }
                ]
            )

        # Clear vision override if we have one
        if session_overrides.get("vision_provider"):
            keyboard.append([{"text": "🔄 Clear vision override", "callback_data": "vis_clear"}])

        keyboard.append(
            [
                {"text": "🔙 Providers", "callback_data": "nav:providers"},
                {"text": "✖ Close", "callback_data": "prov_close"},
            ]
        )

        text_payload = "\n".join(lines)
        if message_id:
            if await self.edit_message(
                chat_id, message_id, text_payload, parse_mode="HTML", keyboard=keyboard
            ) is not None:
                return
        await self.send_message(chat_id, text_payload, parse_mode="HTML", keyboard=keyboard)

    async def _handle_provider_show(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
        text: str = "",
    ) -> None:
        """Show comprehensive routing state — config vs session overrides."""
        try:
            from navig.providers.discovery import (
                _get_active_provider,
                _get_current_tier_assignments,
                resolve_vision_model,
            )
        except Exception:  # noqa: BLE001
            await self.send_message(chat_id, "⚠️ Provider discovery not available.", parse_mode=None)
            return

        session_overrides: dict = {}
        try:
            from navig.gateway.channels.telegram_sessions import get_session_manager

            sm = get_session_manager()
            session_overrides = sm.get_all_session_overrides(chat_id, user_id)
        except Exception:  # noqa: BLE001
            pass

        active_prov = _get_active_provider()
        tier_assignments = _get_current_tier_assignments()
        vision = resolve_vision_model(session_overrides)

        tier_emoji = {"small": "⚡", "big": "🧠", "coder_big": "💻"}
        tier_label = {"small": "Small", "big": "Big", "coder_big": "Code"}

        lines: list[str] = ["<b>📊 Routing State</b>", ""]

        # Active provider
        prov_display = html.escape(active_prov) if active_prov else "<i>none</i>"
        lines.append(f"🎯 Active provider: <code>{prov_display}</code>")
        lines.append("")

        # Config tier assignments
        lines.append("<b>Config (saved):</b>")
        for tier in ("small", "big", "coder_big"):
            if tier in tier_assignments:
                prov_id, model = tier_assignments[tier]
                short = model.split("/")[-1].split(":")[-1] if model else "—"
                lines.append(
                    f"  {tier_emoji[tier]} {tier_label[tier]}: <code>{html.escape(short)}</code> ({html.escape(prov_id)})"
                )
            else:
                lines.append(f"  {tier_emoji[tier]} {tier_label[tier]}: —")

        # Session overrides
        if session_overrides:
            lines.append("")
            lines.append("<b>Session overrides 🔶:</b>")
            for key, value in sorted(session_overrides.items()):
                lines.append(f"  • {html.escape(str(key))}: <code>{html.escape(str(value))}</code>")
            lines.append("")
            lines.append("<i>Session overrides are temporary and lost on restart.</i>")

            # Show effective routing (config + session merged)
            lines.append("")
            lines.append("<b>Effective (after session merge):</b>")
            for tier in ("small", "big", "coder_big"):
                so_prov = session_overrides.get(f"tier_{tier}_provider", "")
                so_model = session_overrides.get(f"tier_{tier}_model", "")
                if so_prov and so_model:
                    short = so_model.split("/")[-1].split(":")[-1]
                    lines.append(
                        f"  {tier_emoji[tier]} {tier_label[tier]}: "
                        f"<code>{html.escape(short)}</code> ({html.escape(so_prov)}) 🔶"
                    )
                elif tier in tier_assignments:
                    prov_id, model = tier_assignments[tier]
                    short = model.split("/")[-1].split(":")[-1] if model else "—"
                    lines.append(
                        f"  {tier_emoji[tier]} {tier_label[tier]}: <code>{html.escape(short)}</code> ({html.escape(prov_id)})"
                    )
                else:
                    lines.append(f"  {tier_emoji[tier]} {tier_label[tier]}: —")
        else:
            lines.append("")
            lines.append("<i>No session overrides active.</i>")

        # Vision
        lines.append("")
        if vision:
            v_prov, v_model, v_reason = vision
            short_v = v_model.split("/")[-1].split(":")[-1]
            lines.append(
                f"👁 Vision: <code>{html.escape(short_v)}</code> ({html.escape(v_prov)}) — {html.escape(v_reason)}"
            )
        else:
            lines.append("👁 Vision: <i>not available</i>")

        keyboard: list[list[dict[str, str]]] = [
            [
                {"text": "🔀 Hybrid", "callback_data": "pu_hybrid"},
                {"text": "👁 Vision", "callback_data": "pu_vision"},
            ],
        ]
        if session_overrides:
            keyboard.append([{"text": "🔄 Reset session", "callback_data": "pu_reset_session"}])
        keyboard.append(
            [
                {"text": "🔙 Providers", "callback_data": "nav:providers"},
                {"text": "✖ Close", "callback_data": "prov_close"},
            ]
        )

        text_payload = "\n".join(lines)
        if message_id:
            if await self.edit_message(
                chat_id, message_id, text_payload, parse_mode="HTML", keyboard=keyboard
            ) is not None:
                return
        await self.send_message(chat_id, text_payload, parse_mode="HTML", keyboard=keyboard)

    async def _handle_provider_reset(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
        text: str = "",
    ) -> None:
        """Clear all session overrides for current chat."""
        count = 0
        try:
            from navig.gateway.channels.telegram_sessions import get_session_manager

            sm = get_session_manager()
            count = sm.clear_session_overrides(chat_id, user_id)
        except Exception:  # noqa: BLE001
            pass

        if count > 0:
            lines = [
                "✅ <b>Session reset</b>",
                "",
                f"Cleared {count} session override{'s' if count != 1 else ''}.",
                "Routing is now using saved config defaults.",
            ]
        else:
            lines = [
                "ℹ️ <b>No session overrides</b>",
                "",
                "Nothing to clear — already using config defaults.",
            ]

        keyboard: list[list[dict[str, str]]] = [
            [
                {"text": "🔙 Providers", "callback_data": "nav:providers"},
                {"text": "✖ Close", "callback_data": "prov_close"},
            ]
        ]

        text_payload = "\n".join(lines)
        if message_id:
            if await self.edit_message(
                chat_id, message_id, text_payload, parse_mode="HTML", keyboard=keyboard
            ) is not None:
                return
        await self.send_message(chat_id, text_payload, parse_mode="HTML", keyboard=keyboard)

    async def _handle_models_reset(
        self,
        chat_id: int,
        user_id: int = 0,
        message_id: int | None = None,
        text: str = "",
    ) -> None:
        """Reset model assignments to curated defaults for the active provider."""
        active_prov = ""
        try:
            from navig.providers.discovery import _get_active_provider

            active_prov = _get_active_provider()
        except Exception:  # noqa: BLE001
            pass

        if not active_prov:
            await self.send_message(
                chat_id,
                "⚠️ No active provider detected. Use /providers to activate one first.",
                parse_mode=None,
            )
            return

        try:
            from navig.providers.registry import get_provider as _gp

            manifest = _gp(active_prov)
        except Exception:  # noqa: BLE001
            manifest = None

        models = await self._resolve_provider_models(active_prov, manifest=manifest)
        if not models:
            await self.send_message(
                chat_id,
                f"⚠️ No models found for {active_prov}.",
                parse_mode=None,
            )
            return

        defaults = self._select_curated_tier_defaults(active_prov, models)
        self._update_llm_mode_router(active_prov, defaults)

        # Also update hybrid router if available
        try:
            from navig.agent.ai_client import get_ai_client

            router = get_ai_client().model_router
            if router and router.is_active:
                for tier, model in defaults.items():
                    slot = router.cfg.slot_for_tier(tier)
                    slot.provider = active_prov
                    slot.model = model
                self._persist_hybrid_router_assignments(router.cfg)
        except Exception:  # noqa: BLE001
            pass

        self._refresh_ai_runtime_after_router_update()

        def _short(m: str) -> str:
            return m.split("/")[-1].split(":")[-1] if m else "—"

        lines = [
            f"✅ <b>Models reset for {active_prov}</b>",
            "",
            f"⚡ Small: <code>{_short(defaults.get('small', ''))}</code>",
            f"🧠 Big: <code>{_short(defaults.get('big', ''))}</code>",
            f"💻 Code: <code>{_short(defaults.get('coder_big', ''))}</code>",
            "",
            "<i>Saved to config. Use /models to customise per tier.</i>",
        ]

        keyboard: list[list[dict[str, str]]] = [
            [
                {"text": "🎛️ Providers", "callback_data": "nav:providers"},
                {"text": "📝 Models", "callback_data": "nav:models"},
            ],
            [{"text": "✖ Close", "callback_data": "prov_close"}],
        ]

        text_payload = "\n".join(lines)
        if message_id:
            if await self.edit_message(
                chat_id, message_id, text_payload, parse_mode="HTML", keyboard=keyboard
            ) is not None:
                return
        await self.send_message(chat_id, text_payload, parse_mode="HTML", keyboard=keyboard)

    # ── End Provider control surface ─────────────────────────────────────

    async def _handle_providers_and_models(
        self, chat_id: int, user_id: int = 0, is_group: bool = False
    ) -> None:
        """Combined view: model routing table + AI provider hub."""
        await self._handle_models_command(chat_id, user_id)
        await self._handle_providers(chat_id, user_id)

    # -- Diagnostics -----------------------------------------------------------

    async def _handle_version(self, chat_id: int) -> None:
        """Show NAVIG version info and active host (/version)."""
        import sys

        lines = ["<b>NAVIG</b>"]
        try:
            import navig as _navig_pkg

            ver = getattr(_navig_pkg, "__version__", "unknown")
            lines.append(f"Version: <code>{ver}</code>")
        except Exception as e:
            lines.append(f"Version: <i>unknown ({e})</i>")

        lines.append(f"Python: <code>{sys.version.split()[0]}</code>")

        # Active host
        try:
            from navig.config import load_config

            cfg = load_config()
            active_host = (cfg.get("active_host") or "—") if cfg else "—"
            lines.append(f"Active host: <code>{active_host}</code>")
        except Exception as _host_exc:  # noqa: BLE001
            logger.debug("active host lookup skipped for /debug: %s", _host_exc)

        # Platform
        import platform

        lines.append(f"Platform: <code>{platform.system()} {platform.machine()}</code>")

        await self.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    async def _handle_debug(self, chat_id: int) -> None:
        """Show daemon debug info (/debug)."""
        import sys

        lines = ["<b>Debug</b>\n"]
        lines.append(f"Python: <code>{sys.version.split()[0]}</code>")
        try:
            import navig as _navig_pkg

            lines.append(f"navig pkg: <code>{getattr(_navig_pkg, '__file__', 'unknown')}</code>")
            lines.append(f"version: <code>{getattr(_navig_pkg, '__version__', 'unknown')}</code>")
        except Exception as e:
            lines.append(f"navig: - <code>{e}</code>")
        try:
            from navig.platform import paths as _paths
            from navig.vault import get_vault

            v = get_vault()
            count = len(v.list()) if hasattr(v, "list") else "?"
            lines.append(f"vault: - <code>{count} entries</code> ({_paths.vault_dir()})")
        except Exception as e:
            try:
                from navig.platform import paths as _paths

                vpath = str(_paths.vault_dir())
            except Exception:
                vpath = "?"
            lines.append(f"vault: - <code>{e}</code> - path: <code>{vpath}</code>")
        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                s_list = sm.list_sessions() if hasattr(sm, "list_sessions") else []
                lines.append(f"sessions: <code>{len(s_list)} loaded</code>")
            except Exception:
                lines.append("sessions: -")
        try:
            from navig.gateway.channels.telegram_voice import _HAS_VOICE as _hv

            lines.append(f"HAS_VOICE: <code>{_hv}</code>")
        except Exception:
            lines.append("HAS_VOICE: <code>unknown</code>")
        lines.append(f"HAS_KEYBOARDS: <code>{_HAS_KEYBOARDS}</code>")
        lines.append(f"HAS_SESSIONS: <code>{_HAS_SESSIONS}</code>")
        pp = os.environ.get("PYTHONPATH", "(not set)")
        lines.append(f"PYTHONPATH: <code>{pp}</code>")
        try:
            from navig.vault.resolver import resolve_secret

            dg = resolve_secret(
                ["DEEPGRAM_KEY", "DEEPGRAM_API_KEY"],
                ["deepgram/api_key", "deepgram/api-key", "deepgram_api_key"],
            )
        except Exception:
            dg = os.environ.get("DEEPGRAM_KEY") or os.environ.get("DEEPGRAM_API_KEY")
        lines.append(f"DEEPGRAM_KEY: <code>{'set' if dg else 'missing'}</code>")
        await self.send_message(chat_id, "\n".join(lines))

    @rate_limited
    @error_handled
    async def _handle_trace(self, chat_id: int, user_id: int) -> None:
        """Show recent activity snapshot (/trace).

        Covers: LLM bridges - recent messages - session state - daemon warnings - vault.
        """
        SEP = "-"
        now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC")
        lines: list = [f"<b>Recent Trace</b> - {now_utc}", ""]

        _bridge_online, _bridge_url = await self._probe_bridge_grid()
        lines.append("<b>Routing</b>")
        lines.append(
            f"  - Bridge Grid <code>{_bridge_url}</code> - <b>online</b>"
            if _bridge_online
            else "  - Bridge Grid - offline (using model router)"
        )

        if self._is_debug_mode(user_id):
            try:
                from navig.llm_router import get_llm_router

                llm_router = get_llm_router()
                _TIER_NAMES = {
                    "small_talk": ("-", "Small"),
                    "big_tasks": ("-", "Big"),
                    "coding": ("-", "Code"),
                }
                if llm_router:
                    for mode_name, (icon, label) in _TIER_NAMES.items():
                        mc = llm_router.modes.get_mode(mode_name)
                        if not mc:
                            continue
                        lines.append(
                            f"  {icon} {label} -> <code>{getattr(mc, 'provider', '?')}:{getattr(mc, 'model', '?')}</code>"
                        )
            except Exception:
                lines.append("  <i>(model router unavailable)</i>")

        lines.append(SEP)

        # -- Session messages - triple-fallback (DUP-10 fix: extracted helper) -
        session_messages = self._load_recent_messages(user_id, chat_id=chat_id)
        all_sessions_count = 0
        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                all_sessions_count = len(sm._sessions)  # _sessions is the private backing dict
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        lines.append(f"<b>Memory</b> - {len(session_messages)} msgs - {all_sessions_count} session(s)")
        lines.append(SEP)

        lines.append("<b>Recent</b>")
        recent = session_messages[-8:]
        if recent:
            for msg in recent:
                role = msg.get("role", "?")
                raw_content = str(msg.get("content") or "").replace("\n", " ").strip()
                preview = (
                    raw_content
                    if len(raw_content) <= 64
                    else raw_content[:64].rsplit(" ", 1)[0] + "-"
                ) or "_(empty)_"
                arrow = "-" if role in ("user", "human") else "-"
                actor = "-" if role in ("user", "human") else "-"
                ts_raw = msg.get("timestamp") or msg.get("ts") or ""
                ts_prefix = ""
                if ts_raw:
                    try:
                        if isinstance(ts_raw, (int, float)):
                            ts_prefix = (
                                datetime.fromtimestamp(ts_raw, tz=timezone.utc).strftime("%H:%M")
                                + " "
                            )
                        else:
                            ts_prefix = str(ts_raw)[:5] + " "
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical
                lines.append(f"  {ts_prefix}{arrow} {actor}: {preview}")
        else:
            lines.append("  <i>(no recent activity)</i>")

        lines.append(SEP)

        if hasattr(self, "_get_user_tier_pref"):
            tier = self._get_user_tier_pref(chat_id, user_id)
        else:
            tier = self._user_model_prefs.get(user_id, "")
        tier_label = {
            "small": "fast",
            "big": "smart",
            "coder_big": "coder",
            "": "auto",
        }.get(tier, tier)
        active_host = "?"
        try:
            from navig.config import get_config_manager

            _gcfg = get_config_manager().global_config or {}
            active_host = _gcfg.get("active_host") or _gcfg.get("default_host") or "?"
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        voice_label = "?"
        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                _voice = sm.get_session_metadata(chat_id, user_id, "voice_replies_enabled", None)
                if _voice is not None:
                    voice_label = "on" if _voice else "off"
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        lines.append(
            f"<b>Session</b> - tier: <code>{tier_label}</code> - host: <code>{html.escape(active_host)}</code> - voice: <code>{voice_label}</code>"
        )
        lines.append(SEP)

        # -- Daemon log warnings ------------------------------------------------
        try:
            from navig.platform.paths import local_log_dir as _lldir

            _log_candidates = [
                str(_lldir() / "debug.log"),
                "/var/log/navig/daemon.log",
                "/var/log/navig-daemon.log",
            ]
        except Exception:
            _log_candidates = ["/var/log/navig/daemon.log"]

        daemon_issues: list = []
        for _log_path in _log_candidates:
            if not os.path.exists(_log_path):
                continue
            try:
                with open(_log_path, encoding="utf-8", errors="replace") as fh:
                    _tail = fh.readlines()[-50:]
                _kw = (
                    "warning",
                    "error",
                    "could not",
                    "permission denied",
                    "no such file",
                    "failed",
                    "critical",
                )
                daemon_issues = [ln.strip() for ln in _tail if any(kw in ln.lower() for kw in _kw)]
                break
            except OSError:
                pass  # best-effort cleanup

        if daemon_issues:
            lines.append("<b>Daemon Warnings</b>")
            for issue in daemon_issues[-5:]:
                display = issue if len(issue) <= 100 else issue[:97] + "-"
                lines.append(f"  -  <code>{display}</code>")
        else:
            lines.append("<b>Daemon</b> - no warnings")

        try:
            from navig.vault import get_vault

            _v = get_vault()
            _items = _v.list() if hasattr(_v, "list") else []
            vault_msg = f"- {len(_items)} entries"
        except Exception as _ve:
            vault_msg = f"- {str(_ve)[:60]}"
        lines.append(f"<b>Vault</b> - {vault_msg}")
        lines.append(SEP)

        trace_keyboard = [
            [
                {"text": "- Refresh", "callback_data": "trace_refresh"},
                {"text": "- Providers", "callback_data": "trace_providers"},
                {"text": "- Model", "callback_data": "trace_model"},
            ],
            [{"text": "- Close", "callback_data": "trace_close"}],
        ]
        await self.send_message(chat_id, "\n".join(lines), keyboard=trace_keyboard)

    def _load_recent_messages(self, user_id: int, chat_id: int = 0) -> list:
        """Load recent session messages via three fallback paths.

        1. SessionManager in-memory cache
        2. navig.agent.memory module
        3. msg_trace.jsonl file

        Returns a list of ``{"role": ..., "content": ...}`` dicts.
        """
        # BUG-15 fix: chat_id was previously referenced but not in scope (NameError
        # silently swallowed by the except block, always falling through to fallback).
        _chat_id = chat_id or user_id  # DM: chat_id == user_id
        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                # Use the real session object via the public API
                raw_session = sm.get_session(_chat_id, user_id)
                if raw_session is not None:
                    return list(raw_session.messages or [])
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        try:
            from navig.agent.memory import get_memory

            mem = get_memory()
            if hasattr(mem, "get_recent"):
                return mem.get_recent(user_id=str(user_id), limit=8) or []
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        trace_file = msg_trace_path()
        if not trace_file.exists():
            return []
        try:
            messages = []
            with open(trace_file, encoding="utf-8") as _f:
                for raw in _f.readlines()[-8:]:
                    try:
                        entry = json.loads(raw)
                        messages.append(
                            {
                                "role": entry.get("role") or entry.get("type", "?"),
                                "content": entry.get("content") or entry.get("text") or "",
                            }
                        )
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical
            return messages
        except Exception:
            return []

    # -- Model tier commands ---------------------------------------------------

    async def _handle_tier_command(self, chat_id: int, user_id: int, cmd: str) -> None:
        """Handle /big /small /coder /auto - set or clear persistent model tier."""
        tier_map = {
            "/big": ("big", "- Big", "next messages will use the large smart model."),
            "/small": (
                "small",
                "- Small",
                "next messages will use the fast lightweight model.",
            ),
            "/coder": (
                "coder_big",
                "- Coder",
                "next messages will use the coder model.",
            ),
            "/auto": ("", "- Auto", "model selection is back on automatic."),
        }
        tier_key, label, note = tier_map[cmd]
        if hasattr(self, "_set_user_tier_pref"):
            self._set_user_tier_pref(chat_id, user_id, tier_key)
        else:
            if tier_key:
                self._user_model_prefs[user_id] = tier_key
            elif user_id in self._user_model_prefs:
                del self._user_model_prefs[user_id]
        await self.send_message(
            chat_id,
            f"{label} - {note}\nSend your message normally now.",
            parse_mode=None,
        )

    @rate_limited
    @error_handled
    @typing_context
    async def _handle_restart(
        self,
        chat_id: int,
        user_id: int,
        metadata: MessageMetadata,
        arg: str,
    ) -> None:
        """/restart [target] - systemd daemon restart or docker container restart.

        SEC-3 fix: sudo password is passed via a dedicated environment variable
        instead of being interpolated into the shell command string.
        """
        DAEMON_ALIASES = {
            "daemon",
            "navig",
            "navig-daemon",
            "navig_daemon",
            "svc",
            "service",
            "",
        }
        target = (arg or "").strip().lower()

        if target in DAEMON_ALIASES:
            # Dynamic countdown: send initial message then edit it each second
            msg = await self.send_message(
                chat_id, "\U0001f504 Restarting navig-daemon in 3s\u2026", parse_mode=None
            )
            msg_id = (msg or {}).get("message_id")
            for remaining in (2, 1):
                await asyncio.sleep(1)
                if msg_id:
                    await self.edit_message(
                        chat_id,
                        msg_id,
                        f"\U0001f504 Restarting navig-daemon in {remaining}s\u2026",
                        parse_mode=None,
                    )
            await asyncio.sleep(1)
            if msg_id:
                await self.edit_message(
                    chat_id,
                    msg_id,
                    "\U0001f504 Restarting navig-daemon now\u2026",
                    parse_mode=None,
                )
            sudo_pass = os.environ.get("SUDO_PASS", "")
            if sudo_pass:
                # Pass password via env var - never interpolated into shell string (SEC-3 fix)
                cmd = [
                    "bash",
                    "-c",
                    "printf '%s\n' \"$_NAVIG_SUDOPW\" | sudo -S systemctl restart navig-daemon",
                ]
                await asyncio.create_subprocess_exec(
                    *cmd,
                    env={**os.environ, "_NAVIG_SUDOPW": sudo_pass},
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
            else:
                await asyncio.create_subprocess_exec(
                    "bash",
                    "-c",
                    "sudo systemctl restart navig-daemon",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
        else:
            await self._handle_cli_command(chat_id, user_id, metadata, f"docker restart {arg}")

    # -- Audio / settings menus ------------------------------------------------

    async def _handle_audio_menu(
        self,
        chat_id: int,
        user_id: int,
        is_group: bool = False,
        message_id: int | None = None,
    ) -> None:
        """Send the /audio response-mode panel."""
        if not _HAS_KEYBOARDS or not _HAS_SESSIONS:
            await self.send_message(
                chat_id,
                "- Audio settings requires the keyboard + session modules.",
                parse_mode=None,
            )
            return

        sm = get_session_manager()
        session = sm.get_or_create_session(chat_id, user_id, is_group)
        keyboard_rows = build_audio_keyboard(session)
        keyboard_rows = list(keyboard_rows)
        keyboard_rows.append(
            [
                {"text": "🔙 Back", "callback_data": "nav:back"},
                {"text": "🏠 Home", "callback_data": "nav:home"},
            ]
        )
        if message_id:
            await self.edit_message(
                chat_id,
                message_id,
                _audio_header_text(session),
                parse_mode="HTML",
                keyboard=keyboard_rows,
            )
            return
        sent = await self.send_message(chat_id, _audio_header_text(session), keyboard=keyboard_rows)
        if sent and isinstance(sent, dict):
            self._get_navigation_state(chat_id)["message_id"] = sent.get("message_id")

    async def _handle_voice_menu(
        self,
        chat_id: int,
        user_id: int,
        is_group: bool = False,
        message_id: int | None = None,
    ) -> None:
        """Send the /voice provider picker (provider -> model -> voice/speed/format)."""
        if _HAS_AUDIO_MENU:
            try:
                cfg = _load_audio_config(user_id)
                keyboard = list(_audio_screen_a_kb(cfg))
                keyboard.append(
                    [
                        {"text": "🔙 Back", "callback_data": "nav:back"},
                        {"text": "🏠 Home", "callback_data": "nav:home"},
                    ]
                )
                if message_id:
                    await self.edit_message(
                        chat_id,
                        message_id,
                        _audio_screen_a_text(cfg),
                        parse_mode="HTML",
                        keyboard=keyboard,
                    )
                else:
                    sent = await self.send_message(
                        chat_id,
                        _audio_screen_a_text(cfg),
                        keyboard=keyboard,
                    )
                    if sent and isinstance(sent, dict):
                        self._get_navigation_state(chat_id)["message_id"] = sent.get("message_id")
                return
            except Exception as _am_err:
                logger.debug("Deep audio menu failed, falling back: %s", _am_err)

        await self._handle_audio_menu(chat_id, user_id, is_group, message_id=message_id)

    # Backward-compat alias
    _handle_settings_menu = _handle_audio_menu

    async def _handle_provider_voice(
        self,
        chat_id: int,
        user_id: int,
        is_group: bool = False,
        message_id: int | None = None,
    ) -> None:
        """/provider_voice — Voice API key status panel (Deepgram, ElevenLabs)."""
        _VOICE_PROVIDERS = [
            {
                "id": "deepgram",
                "label": "Deepgram",
                "role": "STT + TTS",
                "env_keys": ("DEEPGRAM_API_KEY", "DEEPGRAM_KEY"),
            },
            {
                "id": "elevenlabs",
                "label": "ElevenLabs",
                "role": "TTS",
                "env_keys": ("ELEVENLABS_API_KEY", "XI_API_KEY"),
            },
        ]

        import os

        # Resolve vault availability
        vault = None
        try:
            from navig.vault import get_vault as _gv

            vault = _gv()
        except Exception:  # noqa: BLE001
            pass

        def _has_key(prov: dict) -> bool:
            """Return True if a key exists in vault or env."""
            if vault is not None:
                try:
                    cred = vault.get(prov["id"])
                    if cred is not None:
                        return True
                except Exception:  # noqa: BLE001
                    pass
            for env_key in prov["env_keys"]:
                if os.environ.get(env_key, "").strip():
                    return True
            return False

        lines = ["\U0001f3a4 <b>Voice API Providers</b>", ""]
        lines.append("STT \u2014 Speech to Text  |  TTS \u2014 Text to Speech")
        lines.append("")

        keyboard_rows: list[list[dict]] = []
        for prov in _VOICE_PROVIDERS:
            has = _has_key(prov)
            status = "\u2705" if has else "\U0001f512"
            lines.append(f"{status} <b>{prov['label']}</b> \u2014 <code>{prov['role']}</code>")
            if not has:
                lines.append("  <i>No key found — tap to add</i>")
                cb = f"voice_prov_add:{prov['id']}"
            else:
                lines.append("  <i>Key stored in vault</i>")
                cb = f"voice_prov_check:{prov['id']}"
            keyboard_rows.append(
                [
                    {
                        "text": f"{status} {prov['label']}",
                        "callback_data": cb,
                    }
                ]
            )

        lines.append("")
        lines.append("<i>Tap a provider to add or validate its key.</i>")

        keyboard_rows.append(
            [
                {"text": "\U0001f399 /voice settings", "callback_data": "st_goto_voice"},
                {"text": "\u2715 Close", "callback_data": "st_close"},
            ]
        )

        text = "\n".join(lines)
        if message_id:
            await self.edit_message(
                chat_id, message_id, text, parse_mode="HTML", keyboard=keyboard_rows
            )
        else:
            sent = await self.send_message(chat_id, text, keyboard=keyboard_rows)
            if sent and isinstance(sent, dict):
                self._get_navigation_state(chat_id)["message_id"] = sent.get("message_id")

    async def _handle_settings_hub(
        self,
        chat_id: int,
        user_id: int,
        is_group: bool = False,
        message_id: int | None = None,
    ) -> None:
        """Send the /settings pro hub - main navigation panel."""
        if not _HAS_KEYBOARDS or not _HAS_SESSIONS:
            await self.send_message(
                chat_id,
                "- Settings hub requires keyboard + session modules.",
                parse_mode=None,
            )
            return

        sm = get_session_manager()
        session = sm.get_or_create_session(chat_id, user_id, is_group)
        keyboard_rows = build_settings_hub_keyboard(session)
        keyboard_rows = list(keyboard_rows)
        keyboard_rows.append(
            [
                {"text": "🔙 Back", "callback_data": "nav:back"},
                {"text": "🏠 Home", "callback_data": "nav:home"},
            ]
        )
        if message_id:
            await self.edit_message(
                chat_id,
                message_id,
                _settings_hub_text(session),
                parse_mode="HTML",
                keyboard=keyboard_rows,
            )
            return
        sent = await self.send_message(chat_id, _settings_hub_text(session), keyboard=keyboard_rows)
        if sent and isinstance(sent, dict):
            self._get_navigation_state(chat_id)["message_id"] = sent.get("message_id")

    # -- Formatting & Reasoning -------------------------------------------------

    async def _handle_format(
        self,
        chat_id: int,
        user_id: int,
        text: str = "",
    ) -> None:
        """/format [text] — convert Markdown to Telegram Unicode format."""
        from navig.gateway.channels.telegram_formatter import (
            MarkdownFormatter,
            get_formatter_store,
        )

        store = get_formatter_store()
        prefs = store.get(user_id)
        formatter = MarkdownFormatter()

        # Strip the command prefix so bare /format shows settings panel
        text_arg = (
            text[len("/format") :].strip() if text.lower().startswith("/format") else text.strip()
        )

        if not text_arg:
            # Show settings panel if no text provided
            from navig.gateway.channels.telegram_formatter import (
                build_formatter_settings_keyboard,
            )

            keyboard = build_formatter_settings_keyboard(prefs)
            await self.send_message(
                chat_id,
                "<b>Markdown Formatter Settings</b>\n\nSend <code>/format &lt;text&gt;</code> to convert, or adjust preferences below.",
                parse_mode="HTML",
                keyboard=keyboard,
            )
            return

        chunks = formatter.convert_chunked(text_arg, prefs)  # text_arg stripped above
        for chunk in chunks:
            await self.send_message(chat_id, chunk, parse_mode=None)

    async def _handle_think(
        self,
        chat_id: int,
        user_id: int,
        text: str = "",
        metadata: Any = None,
    ) -> None:
        """/think <topic> — reason via LLM, output in paginated cards."""
        topic = text[len("/think") :].strip() if text.lower().startswith("/think") else text.strip()
        if not topic:
            await self.send_message(
                chat_id,
                "Usage: <code>/think &lt;your topic or question&gt;</code>\n\nI'll reason through it step by step, delivered as swipeable cards.",
                parse_mode="HTML",
            )
            return

        await self.send_typing(chat_id)

        # Call LLM via on_message routing
        llm_text = ""
        if self.on_message:
            try:
                llm_text = await self.on_message(
                    channel="telegram",
                    user_id=str(user_id),
                    message=f"Think step by step about: {topic}",
                    metadata=metadata,
                )
            except Exception as exc:
                logger.warning("_handle_think LLM error: %s", exc)

        if not llm_text:
            await self.send_message(
                chat_id, "- Unable to generate reasoning output.", parse_mode=None
            )
            return

        from navig.gateway.channels.telegram_navigator import CardNavigator

        nav = CardNavigator(self)
        await nav.create(chat_id=chat_id, user_id=user_id, topic=topic, llm_text=llm_text)

    async def _handle_refine_cmd(
        self,
        chat_id: int,
        user_id: int,
        text: str = "",
        metadata: Any = None,
    ) -> None:
        """/refine [text] — start the AI clarification + refinement loop."""
        topic = (
            text[len("/refine") :].strip() if text.lower().startswith("/refine") else text.strip()
        )
        if not topic:
            await self.send_message(
                chat_id,
                "Usage: <code>/refine &lt;your text or idea&gt;</code>\n\nI'll ask 3 clarifying questions, then produce a refined version.",
                parse_mode="HTML",
            )
            return

        from navig.gateway.channels.telegram_refiner import RefinementEngine

        engine = RefinementEngine(self)
        await engine.start(
            chat_id=chat_id,
            user_id=user_id,
            text=topic,
            topic="",
        )

    async def _handle_weather(
        self,
        chat_id: int,
        user_id: int,
        text: str = "",
        metadata: Any = None,
    ) -> None:
        """/weather [city] — show current weather via wttr.in."""
        city = (
            text[len("/weather") :].strip() if text.lower().startswith("/weather") else text.strip()
        )
        if city:
            import re as _re

            safe_city = _re.sub(r"[^A-Za-z0-9\s\-]", "", city).strip().replace(" ", "+")
            url = f"wttr.in/{safe_city}?format=4"
        else:
            url = "wttr.in/?format=4"

        await self._handle_cli_command(
            chat_id,
            user_id,
            metadata or {},
            f"run \"curl -s '{url}'\"",
        )

    async def _handle_docker_cmd(
        self,
        chat_id: int,
        user_id: int,
        text: str = "",
        metadata: Any = None,
    ) -> None:
        """/docker [ps|logs <name>|restart <name>|all|<name>] — smart container dispatch."""
        arg = text[len("/docker") :].strip() if text.lower().startswith("/docker") else text.strip()

        if not arg or arg.lower() in ("ps", "all", "list"):
            cli_cmd = "docker ps"
        elif arg.lower().startswith("logs "):
            name = arg[5:].strip()
            cli_cmd = f"docker logs {name} -n 50"
        elif arg.lower().startswith("restart "):
            name = arg[8:].strip()
            cli_cmd = f"docker restart {name}"
        elif arg.lower().startswith("stop "):
            name = arg[5:].strip()
            cli_cmd = f"docker stop {name}"
        elif arg.lower().startswith("start "):
            name = arg[6:].strip()
            cli_cmd = f"docker start {name}"
        else:
            # Treat bare arg as container name — show its logs
            cli_cmd = f"docker logs {arg} -n 50"

        await self._handle_cli_command(chat_id, user_id, metadata or {}, cli_cmd)

    # -- Monitoring helpers ----------------------------------------------------

    @staticmethod
    def _mon_bar(pct: float, width: int = 14) -> str:
        """Unicode progress bar, e.g. ████░░░░░░░░░░ for 28 %."""
        filled = int(round(pct / 100 * width))
        return "█" * filled + "░" * (width - filled)

    @staticmethod
    def _mon_status_icon(pct: float, high: int = 80, med: int = 60) -> str:
        if pct >= high:
            return "🔴"
        if pct >= med:
            return "🟡"
        return "🟢"

    @staticmethod
    def _mon_header(icon: str, title: str, subtitle: str = "") -> str:
        line = "━" * 26
        sub = f"\n<i>{html.escape(subtitle)}</i>" if subtitle else ""
        return f"{line}\n{icon}  <b>{html.escape(title)}</b>{sub}\n{line}"

    @staticmethod
    def _mon_host_ctx() -> tuple:
        """Return (host_name, server_config_dict, is_local)."""
        try:
            from navig.config import get_config_manager
            from navig.remote import is_local_host

            cfg = get_config_manager()
            host_name = cfg.get_active_host() or "localhost"
            server_config = cfg.load_server_config(host_name)
            is_local = is_local_host(server_config)
            return host_name, server_config, is_local
        except Exception:
            return "localhost", {"is_local": True}, True

    async def _send_monitor_card(
        self,
        chat_id: int,
        text_payload: str,
        keyboard: list[list[dict[str, str]]],
        message_id: int | None = None,
    ) -> None:
        """Send monitoring card, editing in-place when message_id is provided."""
        if message_id:
            try:
                if await self.edit_message(
                    chat_id,
                    message_id,
                    text_payload,
                    parse_mode="HTML",
                    keyboard=keyboard,
                ) is not None:
                    return
            except Exception as exc:  # noqa: BLE001
                logger.debug("Monitor card in-place edit failed: %s", exc)
        await self.send_message(chat_id, text_payload, parse_mode="HTML", keyboard=keyboard)

    # -- Monitoring command handlers -------------------------------------------

    @rate_limited
    @error_handled
    @typing_context
    async def _handle_disk_cmd(
        self,
        chat_id: int,
        user_id: int,
        metadata: MessageMetadata,
        message_id: int | None = None,
    ) -> None:
        """Rich disk usage card (/disk /df)."""
        host_name, _server_config, is_local = self._mon_host_ctx()
        lines: list = [self._mon_header("💽", "Disk Usage", host_name)]

        if is_local:
            try:
                import psutil

                partitions = psutil.disk_partitions(all=False)
                shown = 0
                for part in partitions:
                    if shown >= 6:
                        break
                    try:
                        usage = psutil.disk_usage(part.mountpoint)
                        pct = usage.percent
                        icon = self._mon_status_icon(pct)
                        bar = self._mon_bar(pct)
                        used_gb = usage.used / (1024 ** 3)
                        total_gb = usage.total / (1024 ** 3)
                        lines.append(
                            f"\n{icon} <b>{html.escape(part.mountpoint)}</b>  {pct:.0f}%\n"
                            f"<code>{bar}</code>\n"
                            f"  {used_gb:.1f} GB used / {total_gb:.1f} GB total"
                        )
                        shown += 1
                    except (PermissionError, OSError):
                        pass
                if not shown:
                    lines.append("<i>No accessible partitions found</i>")
            except ImportError:
                lines.append("<i>psutil not available — install it with: pip install psutil</i>")
            except Exception as exc:
                lines.append(f"<i>Disk info error: {html.escape(str(exc))}</i>")
        else:
            await self._handle_cli_command(
                chat_id, user_id, metadata or {}, "host monitor show --disk"
            )
            return

        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        lines.append(f"\n<i>─ Updated {now_str} ─</i>")
        keyboard = [
            [
                {"text": "🔄 Refresh", "callback_data": "slash:disk"},
                {"text": "🧠 Memory", "callback_data": "slash:memory"},
            ],
            [
                {"text": "⚡ CPU", "callback_data": "slash:cpu"},
                {"text": "🔌 Ports", "callback_data": "slash:ports"},
            ],
        ]
        await self._send_monitor_card(chat_id, "\n".join(lines), keyboard, message_id=message_id)

    @rate_limited
    @error_handled
    @typing_context
    async def _handle_memory_cmd(
        self,
        chat_id: int,
        user_id: int,
        metadata: MessageMetadata,
        message_id: int | None = None,
    ) -> None:
        """Rich memory usage card (/memory)."""
        host_name, _server_config, is_local = self._mon_host_ctx()
        lines: list = [self._mon_header("🧠", "Memory Status", host_name)]

        if is_local:
            try:
                import psutil

                vm = psutil.virtual_memory()
                sw = psutil.swap_memory()

                ram_pct = vm.percent
                ram_icon = self._mon_status_icon(ram_pct)
                ram_bar = self._mon_bar(ram_pct)
                ram_used = vm.used / (1024 ** 3)
                ram_total = vm.total / (1024 ** 3)
                ram_avail = vm.available / (1024 ** 3)

                lines.append(
                    f"\n{ram_icon} <b>RAM</b>  {ram_pct:.0f}%\n"
                    f"<code>{ram_bar}</code>\n"
                    f"  Used: {ram_used:.1f} GB  /  Total: {ram_total:.1f} GB\n"
                    f"  Available: {ram_avail:.1f} GB"
                )

                if sw.total > 0:
                    sw_pct = sw.percent
                    sw_icon = self._mon_status_icon(sw_pct)
                    sw_bar = self._mon_bar(sw_pct)
                    sw_used = sw.used / (1024 ** 3)
                    sw_total = sw.total / (1024 ** 3)
                    lines.append(
                        f"\n{sw_icon} <b>Swap</b>  {sw_pct:.0f}%\n"
                        f"<code>{sw_bar}</code>\n"
                        f"  Used: {sw_used:.1f} GB  /  Total: {sw_total:.1f} GB"
                    )
                else:
                    lines.append("\n<i>Swap: not configured</i>")
            except ImportError:
                lines.append("<i>psutil not available — install it with: pip install psutil</i>")
            except Exception as exc:
                lines.append(f"<i>Memory info error: {html.escape(str(exc))}</i>")
        else:
            await self._handle_cli_command(
                chat_id, user_id, metadata or {}, 'run "free -h"'
            )
            return

        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        lines.append(f"\n<i>─ Updated {now_str} ─</i>")
        keyboard = [
            [
                {"text": "🔄 Refresh", "callback_data": "slash:memory"},
                {"text": "💽 Disk", "callback_data": "slash:disk"},
            ],
            [
                {"text": "⚡ CPU", "callback_data": "slash:cpu"},
                {"text": "🕐 Uptime", "callback_data": "slash:uptime"},
            ],
        ]
        await self._send_monitor_card(chat_id, "\n".join(lines), keyboard, message_id=message_id)

    @rate_limited
    @error_handled
    @typing_context
    async def _handle_cpu_cmd(
        self,
        chat_id: int,
        user_id: int,
        metadata: MessageMetadata,
        message_id: int | None = None,
    ) -> None:
        """Rich CPU & load card (/cpu /top)."""
        import platform as _pf

        host_name, _server_config, is_local = self._mon_host_ctx()
        lines: list = [self._mon_header("⚡", "CPU & Load", host_name)]

        if is_local:
            try:
                import psutil

                cpu_pct = psutil.cpu_percent(interval=0.5)
                cpu_icon = self._mon_status_icon(cpu_pct)
                cpu_bar = self._mon_bar(cpu_pct)
                cpu_count = psutil.cpu_count(logical=True)
                cpu_phys = psutil.cpu_count(logical=False)

                lines.append(
                    f"\n{cpu_icon} <b>CPU</b>  {cpu_pct:.0f}%\n"
                    f"<code>{cpu_bar}</code>\n"
                    f"  {cpu_phys} physical / {cpu_count} logical cores"
                )

                # Per-core (up to 8)
                per_core = psutil.cpu_percent(percpu=True)
                if per_core and len(per_core) > 1:
                    core_strs = []
                    for i, pct in enumerate(per_core[:8]):
                        c_icon = "🔴" if pct >= 80 else ("🟡" if pct >= 50 else "🟢")
                        core_strs.append(f"[{i}{c_icon}{pct:.0f}%]")
                    lines.append(f"\n<b>Cores:</b> {'  '.join(core_strs)}")

                # CPU frequency
                try:
                    freq = psutil.cpu_freq()
                    if freq:
                        lines.append(
                            f"\n<b>Freq:</b> {freq.current:.0f} MHz"
                            + (f"  (max {freq.max:.0f} MHz)" if freq.max else "")
                        )
                except Exception:
                    pass

                # Load average (not on Windows)
                if _pf.system() != "Windows":
                    try:
                        load = psutil.getloadavg()
                        lines.append(
                            f"\n<b>Load avg:</b> <code>{load[0]:.2f}  {load[1]:.2f}  {load[2]:.2f}</code>  (1m 5m 15m)"
                        )
                    except Exception:
                        pass

                # Top 5 CPU-hungry processes
                try:
                    procs = sorted(
                        psutil.process_iter(["pid", "name", "cpu_percent"]),
                        key=lambda p: p.info.get("cpu_percent") or 0,
                        reverse=True,
                    )[:5]
                    if procs:
                        lines.append("\n<b>Top processes:</b>")
                        for p in procs:
                            pname = html.escape((p.info.get("name") or "?")[:20])
                            ppct = p.info.get("cpu_percent") or 0.0
                            lines.append(f"  <code>{p.info['pid']:>6}  {pname:<20}  {ppct:.1f}%</code>")
                except Exception:
                    pass

            except ImportError:
                lines.append("<i>psutil not available — install it with: pip install psutil</i>")
            except Exception as exc:
                lines.append(f"<i>CPU info error: {html.escape(str(exc))}</i>")
        else:
            await self._handle_cli_command(
                chat_id, user_id, metadata or {}, 'run "uptime"'
            )
            return

        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        lines.append(f"\n<i>─ Updated {now_str} ─</i>")
        keyboard = [
            [
                {"text": "🔄 Refresh", "callback_data": "slash:cpu"},
                {"text": "🧠 Memory", "callback_data": "slash:memory"},
            ],
            [
                {"text": "💽 Disk", "callback_data": "slash:disk"},
                {"text": "⚙️ Services", "callback_data": "slash:services"},
            ],
        ]
        await self._send_monitor_card(chat_id, "\n".join(lines), keyboard, message_id=message_id)

    @rate_limited
    @error_handled
    @typing_context
    async def _handle_uptime_cmd(
        self,
        chat_id: int,
        user_id: int,
        metadata: MessageMetadata,
        message_id: int | None = None,
    ) -> None:
        """Server uptime card (/uptime)."""
        host_name, _server_config, is_local = self._mon_host_ctx()
        lines: list = [self._mon_header("🕐", "Server Uptime", host_name)]

        if is_local:
            try:
                import psutil

                boot_ts = psutil.boot_time()
                boot_dt = datetime.fromtimestamp(boot_ts, tz=timezone.utc)
                now_dt = datetime.now(timezone.utc)
                delta = now_dt - boot_dt

                days = delta.days
                hours, rem = divmod(delta.seconds, 3600)
                minutes, _ = divmod(rem, 60)

                if days > 0:
                    uptime_str = f"{days}d {hours}h {minutes}m"
                elif hours > 0:
                    uptime_str = f"{hours}h {minutes}m"
                else:
                    uptime_str = f"{minutes}m"

                lines.append(
                    f"\n🟢 <b>Up for:</b> {uptime_str}\n"
                    f"  <b>Since:</b> <code>{boot_dt.strftime('%Y-%m-%d %H:%M UTC')}</code>"
                )

                # Logged-in users
                try:
                    users = psutil.users()
                    if users:
                        lines.append(f"\n<b>Logged in users:</b> {len(users)}")
                        for u in users[:3]:
                            lines.append(
                                f"  {html.escape(u.name)} @ {html.escape(u.terminal or '?')}"
                            )
                except Exception:
                    pass

            except ImportError:
                lines.append("<i>psutil not available — install it with: pip install psutil</i>")
            except Exception as exc:
                lines.append(f"<i>Uptime error: {html.escape(str(exc))}</i>")
        else:
            await self._handle_cli_command(
                chat_id, user_id, metadata or {}, 'run "uptime -p"'
            )
            return

        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        lines.append(f"\n<i>─ Updated {now_str} ─</i>")
        keyboard = [
            [
                {"text": "🔄 Refresh", "callback_data": "slash:uptime"},
                {"text": "⚡ CPU", "callback_data": "slash:cpu"},
            ],
            [
                {"text": "🧠 Memory", "callback_data": "slash:memory"},
                {"text": "💽 Disk", "callback_data": "slash:disk"},
            ],
        ]
        await self._send_monitor_card(chat_id, "\n".join(lines), keyboard, message_id=message_id)

    @rate_limited
    @error_handled
    @typing_context
    async def _handle_services_cmd(
        self,
        chat_id: int,
        user_id: int,
        metadata: MessageMetadata,
        message_id: int | None = None,
    ) -> None:
        """Running services card (/services)."""
        import platform as _pf
        import subprocess

        host_name, _server_config, is_local = self._mon_host_ctx()
        lines: list = [self._mon_header("⚙️", "Running Services", host_name)]

        if is_local:
            if _pf.system() == "Windows":
                try:
                    import psutil

                    svcs = [s for s in psutil.win_service_iter() if s.status() == "running"]
                    svcs_sorted = sorted(svcs, key=lambda s: s.name())[:20]
                    lines.append(f"\n🟢 <b>{len(svcs)} running services</b> (showing up to 20)\n")
                    for svc in svcs_sorted:
                        try:
                            lines.append(f"  • {html.escape(svc.display_name()[:44])}")
                        except Exception:
                            pass
                except ImportError:
                    lines.append("<i>psutil not available — install it with: pip install psutil</i>")
                except Exception as exc:
                    lines.append(f"<i>Service list error: {html.escape(str(exc))}</i>")
            else:
                # Local Linux: systemctl
                try:
                    result = subprocess.run(
                        [
                            "systemctl",
                            "list-units",
                            "--type=service",
                            "--state=running",
                            "--no-pager",
                            "--no-legend",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=_PROBE_TIMEOUT,
                    )
                    svc_lines = [
                        ln.split()[0]
                        for ln in result.stdout.strip().splitlines()
                        if ln.strip()
                    ][:20]
                    lines.append(f"\n🟢 <b>{len(svc_lines)} running services</b>\n")
                    for svc in svc_lines:
                        lines.append(f"  • {html.escape(svc)}")
                except Exception as exc:
                    lines.append(f"<i>Service list error: {html.escape(str(exc))}</i>")
        else:
            await self._handle_cli_command(
                chat_id,
                user_id,
                metadata or {},
                'run "systemctl list-units --type=service --state=running --no-pager | head -40"',
            )
            return

        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        lines.append(f"\n<i>─ Updated {now_str} ─</i>")
        keyboard = [
            [
                {"text": "🔄 Refresh", "callback_data": "slash:services"},
                {"text": "🔌 Ports", "callback_data": "slash:ports"},
            ],
            [
                {"text": "⚡ CPU", "callback_data": "slash:cpu"},
                {"text": "💽 Disk", "callback_data": "slash:disk"},
            ],
        ]
        await self._send_monitor_card(chat_id, "\n".join(lines), keyboard, message_id=message_id)

    @rate_limited
    @error_handled
    @typing_context
    async def _handle_ports_cmd(
        self,
        chat_id: int,
        user_id: int,
        metadata: MessageMetadata,
        message_id: int | None = None,
    ) -> None:
        """Listening ports card (/ports)."""
        host_name, _server_config, is_local = self._mon_host_ctx()
        lines: list = [self._mon_header("🔌", "Listening Ports", host_name)]

        if is_local:
            try:
                import psutil

                conns = [c for c in psutil.net_connections(kind="inet") if c.status == "LISTEN"]
                conns_sorted = sorted(
                    conns, key=lambda c: c.laddr.port if c.laddr else 0
                )[:20]

                if not conns_sorted:
                    lines.append("\n<i>No listening ports found</i>")
                else:
                    lines.append(f"\n🟢 <b>{len(conns)} listening ports</b> (showing up to 20)\n")
                    lines.append("<code>PORT      ADDR               PID   PROCESS</code>")
                    lines.append("<code>──────────────────────────────────────────</code>")
                    for c in conns_sorted:
                        try:
                            port = c.laddr.port if c.laddr else "?"
                            addr = c.laddr.ip if c.laddr else "?"
                            pid = c.pid or "?"
                            pname = ""
                            if c.pid:
                                try:
                                    pname = psutil.Process(c.pid).name()[:14]
                                except Exception:
                                    pass
                            addr_str = addr if addr and addr not in ("0.0.0.0", "::") else "*"
                            lines.append(
                                f"<code>{str(port):<9} {addr_str:<18} {str(pid):<5}</code> {html.escape(pname)}"
                            )
                        except Exception:
                            pass
            except ImportError:
                lines.append("<i>psutil not available — install it with: pip install psutil</i>")
            except Exception as exc:
                lines.append(f"<i>Ports error: {html.escape(str(exc))}</i>")
        else:
            await self._handle_cli_command(
                chat_id, user_id, metadata or {}, 'run "ss -tlnp | head -30"'
            )
            return

        now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        lines.append(f"\n<i>─ Updated {now_str} ─</i>")
        keyboard = [
            [
                {"text": "🔄 Refresh", "callback_data": "slash:ports"},
                {"text": "⚙️ Services", "callback_data": "slash:services"},
            ],
            [
                {"text": "⚡ CPU", "callback_data": "slash:cpu"},
                {"text": "🧠 Memory", "callback_data": "slash:memory"},
            ],
        ]
        await self._send_monitor_card(chat_id, "\n".join(lines), keyboard, message_id=message_id)

    # -- Briefing / deck -------------------------------------------------------

    @rate_limited
    @error_handled
    @typing_context
    async def _handle_briefing(self, chat_id: int, user_id: int, metadata: MessageMetadata) -> None:
        """Real-data system briefing - no AI, no invented content (/briefing)."""
        now = datetime.now(timezone.utc)
        lines: list = [
            f"<b>System Briefing</b> - {now.strftime('%H:%M UTC, %d %b')}",
            "-" * 22,
        ]

        try:
            p = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "systemctl",
                    "show",
                    "navig-daemon",
                    "--property=ActiveState,ActiveEnterTimestamp",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=3.0,
            )
            stdout, _ = await p.communicate()
            state, since = "unknown", ""
            for ln in stdout.decode().splitlines():
                if ln.startswith("ActiveState="):
                    state = ln.split("=", 1)[1].strip()
                if ln.startswith("ActiveEnterTimestamp="):
                    raw = ln.split("=", 1)[1].strip()
                    if raw and raw != "n/a":
                        since = f" - since {raw.split()[-2]}"
            icon = "✅" if state == "active" else "❌"
            lines.append(f"{icon} <b>Daemon:</b> {state}{since}")
        except Exception:
            lines.append("- <b>Daemon:</b> status unavailable")

        try:
            from navig.providers.bridge_grid_reader import (
                BRIDGE_DEFAULT_PORT,
                get_llm_port,
            )

            bridge_port = get_llm_port() or BRIDGE_DEFAULT_PORT
        except Exception:
            from navig.providers.bridge_grid_reader import BRIDGE_DEFAULT_PORT

            bridge_port = BRIDGE_DEFAULT_PORT
        try:
            _s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _s.settimeout(0.8)
            bridge_ok = _s.connect_ex(("127.0.0.1", bridge_port)) == 0
            _s.close()
        except Exception:
            bridge_ok = False
        lines.append(f"- <b>LLM Bridge:</b> {'online (bridge_copilot)' if bridge_ok else 'offline'}")

        try:
            from navig.vault import get_vault

            v = get_vault()
            key_count = len(v.list()) if hasattr(v, "list") else "?"
            lines.append(f"- <b>Vault:</b> {key_count} keys stored")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                lines.append(f"- <b>Sessions:</b> {len(sm.sessions)} active")
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        try:
            p = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "uptime",
                    "-p",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=_SHORT_CMD_TIMEOUT,
            )
            stdout, _ = await p.communicate()
            lines.append(f"- <b>Server:</b> {stdout.decode().strip()}")
        except Exception:  # noqa: BLE001
            # Fallback for Windows (no `uptime` command)
            try:
                import platform as _pf

                import psutil as _psutil

                _bt = _psutil.boot_time()
                _delta = datetime.now(timezone.utc) - datetime.fromtimestamp(_bt, tz=timezone.utc)
                _d, _r = divmod(int(_delta.total_seconds()), 86400)
                _h, _r = divmod(_r, 3600)
                _m = _r // 60
                lines.append(f"- <b>Server:</b> up {_d}d {_h}h {_m}m ({_pf.system()})")
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        try:
            p = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "df",
                    "-h",
                    "/",
                    "--output=used,avail,pcent",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=_SHORT_CMD_TIMEOUT,
            )
            stdout, _ = await p.communicate()
            dfl = stdout.decode().strip().splitlines()
            if len(dfl) >= 2:
                parts = dfl[1].split()
                if len(parts) >= 3:
                    lines.append(f"- <b>Disk:</b> {parts[0]} used, {parts[1]} free ({parts[2]})")
        except Exception:  # noqa: BLE001
            # Fallback for Windows (no `df` command)
            try:
                import platform as _pf

                import psutil as _psutil

                _root = "C:\\" if _pf.system() == "Windows" else "/"
                _du = _psutil.disk_usage(_root)
                _used_gb = _du.used / (1024 ** 3)
                _free_gb = _du.free / (1024 ** 3)
                lines.append(
                    f"- <b>Disk:</b> {_used_gb:.1f} GB used, {_free_gb:.1f} GB free ({_du.percent:.0f}%)"
                )
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        lines.append("-" * 22)

        recent: list = []
        trace_file = msg_trace_path()
        if trace_file.exists():
            try:
                with open(trace_file, encoding="utf-8") as _tf:
                    for raw in _tf.readlines()[-20:]:
                        try:
                            e = json.loads(raw)
                            role = e.get("role") or e.get("type", "")
                            content = str(e.get("content") or e.get("text") or "")[:60]
                            if role in ("user", "human") and content.startswith("/"):
                                recent.append(f"  - <code>{html.escape(content)}</code>")
                        except Exception:  # noqa: BLE001
                            pass  # best-effort; failure is non-critical
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        if recent:
            lines.append("<b>Recent commands:</b>")
            lines.extend(recent[-5:])
        else:
            lines.append("<i>No recent command history.</i>")

        try:
            from navig.spaces.briefing import build_spaces_briefing_lines

            space_lines = build_spaces_briefing_lines(max_items=5)
            if space_lines:
                lines.append("-" * 22)
                lines.extend(space_lines)
        except Exception as _spaces_exc:  # noqa: BLE001
            logger.debug("spaces briefing section skipped: %s", _spaces_exc)

        result = await self.send_message(chat_id, "\n".join(lines))

        # Auto-pin briefing in group chats when enabled
        await self._auto_pin_briefing(chat_id, user_id, result)

    async def _auto_pin_briefing(
        self, chat_id: int, user_id: int, send_result: dict | None
    ) -> None:
        """Pin the just-sent briefing message in group chats (best-effort).

        Unpins the previous briefing before pinning the new one so the pinned
        slot doesn't pile up.  Stores the message_id in session metadata so we
        can unpin it next time.
        """
        if not send_result or not isinstance(send_result, dict):
            return
        msg_id = send_result.get("message_id")
        if not msg_id:
            return

        # Check config
        try:
            from navig.config import get_config_manager

            tg = get_config_manager().get("telegram") or {}
            if not tg.get("auto_pin_briefings", True):
                return
        except Exception:  # noqa: BLE001
            pass  # default: enabled

        # Only pin in group / supergroup chats
        try:
            from navig.gateway.channels.telegram import TelegramChannel

            if not TelegramChannel._is_group_chat_id(chat_id):
                return
        except Exception:  # noqa: BLE001
            if chat_id >= 0:
                return  # positive chat_ids are DMs

        # Unpin previous briefing if tracked
        try:
            from navig.gateway.channels.telegram_sessions import get_session_manager

            sm = get_session_manager()
            prev_id = sm.get_session_metadata(chat_id, 0, "pinned_briefing_msg_id", is_group=True)
            if prev_id:
                await self._api_call("unpinChatMessage", {"chat_id": chat_id, "message_id": prev_id})
        except Exception:  # noqa: BLE001
            pass  # best-effort

        # Pin the new briefing
        try:
            pin_result = await self._api_call(
                "pinChatMessage",
                {"chat_id": chat_id, "message_id": msg_id, "disable_notification": True},
            )
            if pin_result is not None:
                from navig.gateway.channels.telegram_sessions import get_session_manager

                sm = get_session_manager()
                sm.set_session_metadata(
                    chat_id, 0, "pinned_briefing_msg_id", msg_id, is_group=True
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("auto_pin_briefing failed for chat=%s: %s", chat_id, exc)

    async def _handle_pin_cmd(
        self, chat_id: int, user_id: int, metadata: "MessageMetadata"
    ) -> None:
        """/pin — pin the last bot message in this group chat."""
        try:
            from navig.gateway.channels.telegram import TelegramChannel

            is_group = TelegramChannel._is_group_chat_id(chat_id)
        except Exception:  # noqa: BLE001
            is_group = chat_id < 0

        if not is_group:
            await self.send_message(
                chat_id,
                "📌 <code>/pin</code> works in group and supergroup chats only.",
                parse_mode="HTML",
            )
            return

        # Find the most recent assistant message_id from session
        try:
            from navig.gateway.channels.telegram_sessions import get_session_manager

            sm = get_session_manager()
            session = sm.get_or_create_session(chat_id, user_id, is_group=True)
            msg_id = None
            for msg in reversed(session.messages):
                if msg.role == "assistant" and msg.message_id:
                    msg_id = msg.message_id
                    break
        except Exception:  # noqa: BLE001
            msg_id = None

        if not msg_id:
            await self.send_message(
                chat_id,
                "📌 No recent bot message found to pin. Reply directly to pin a message.",
                parse_mode=None,
            )
            return

        pin_result = await self._api_call(
            "pinChatMessage",
            {"chat_id": chat_id, "message_id": msg_id, "disable_notification": True},
        )
        if pin_result is not None:
            await self.send_message(chat_id, "📌 Pinned.", parse_mode=None)
        else:
            await self.send_message(
                chat_id,
                "📌 Could not pin — check that I have admin rights in this group.",
                parse_mode=None,
            )

    async def _handle_deck(self, chat_id: int) -> None:
        """Send a WebApp button to open the Deck (/deck)."""
        deck_url = self._get_deck_url()
        if deck_url:
            await self.send_message(
                chat_id,
                "-opening the deck.",
                parse_mode=None,
                keyboard=[[{"text": "- Open Deck", "web_app": {"url": deck_url}}]],
            )
        else:
            await self.send_message(
                chat_id,
                "-deck not configured yet. set `telegram.deck_url` in config.",
                parse_mode=None,
            )

    # -- Skills ----------------------------------------------------------------

    @rate_limited
    @error_handled
    async def _handle_skill(
        self,
        chat_id: int,
        user_id: int,
        arg: str,
        metadata: MessageMetadata,
    ) -> None:
        """/skill [list | <id> | <id> <command> [args...]]"""
        parts = arg.split()

        if not parts or parts[0].lower() in ("list", "ls", "help"):
            await self._skill_list(chat_id)
            return

        skill_id = parts[0].lower()
        command = parts[1] if len(parts) > 1 else ""
        extra_args = parts[2:] if len(parts) > 2 else []

        skill_name = skill_id
        try:
            from navig.skills.loader import skills_by_id

            index = skills_by_id()
            if skill_id in index:
                skill_name = index[skill_id].name
            elif not command:
                available = "\n".join(
                    f"  <code>{s.id}</code> - {s.name}"
                    for s in sorted(index.values(), key=lambda x: x.id)[:20]
                )
                await self.send_message(
                    chat_id,
                    f"❌ Skill <code>{html.escape(skill_id)}</code> not found.\n\nAvailable:\n{available}",
                )
                return
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        tool_args: dict = {
            "skill_id": skill_id,
            "command": command,
            "extra_args": extra_args,
        }
        await self.send_typing(chat_id)

        try:
            from navig.tools.skill_runner import SkillRunTool

            collector: list[str] = []

            async def _on_status(step: str, detail: str, progress: int) -> None:
                collector.append(f"[{progress:3d}%] {step}: {detail}")

            result = await SkillRunTool().run(tool_args, on_status=_on_status)

            if result.success:
                output_text = ""
                if isinstance(result.output, dict):
                    output_text = result.output.get("output") or result.output.get("info") or ""
                else:
                    output_text = str(result.output or "")

                header = f"<b>{html.escape(skill_name)}</b>" + (f" › <code>{html.escape(command)}</code>" if command else "")
                safe_output = html.escape(output_text[:3800])
                msg = f"{header}\n\n<pre>{safe_output}</pre>" if output_text else f"{header}\n✅ Done."
                await self.send_message(chat_id, msg)
            else:
                await self.send_message(chat_id, f"- Skill error:\n{result.error}", parse_mode=None)

        except Exception as exc:
            await self.send_message(chat_id, f"- /skill crashed: {exc}", parse_mode=None)

    async def _skill_list(self, chat_id: int) -> None:
        """Send a paginated list of all available skills."""
        try:
            from navig.skills.loader import load_all_skills

            skills = load_all_skills()
        except Exception as exc:
            await self.send_message(chat_id, f"- Could not load skills: {exc}", parse_mode=None)
            return

        if not skills:
            await self.send_message(
                chat_id,
                "No skills found.\n\nInstall community skill packs or check your `.navig/skills/` folder.",
                parse_mode=None,
            )
            return

        by_cat: dict[str, list] = {}
        for skill in sorted(skills, key=lambda s: (s.category, s.id)):
            by_cat.setdefault(skill.category, []).append(skill)

        lines: list[str] = ["<b>Available Skills</b>\n"]
        for cat, cat_skills in sorted(by_cat.items()):
            lines.append(f"\n<b>{cat.title()}</b>")
            for s in cat_skills:
                safety_icon = {"safe": "-", "elevated": "-", "destructive": "-"}.get(s.safety, "-")
                lines.append(f"  {safety_icon} <code>{s.id}</code> - {s.name}")

        lines.append("\n\nUsage: <code>/skill &lt;id&gt;</code> for info - <code>/skill &lt;id&gt; &lt;command&gt;</code> to run")
        await self.send_message(chat_id, "\n".join(lines))

    # -- CLI command dispatch --------------------------------------------------

    def _match_cli_command(self, text: str) -> str | None:
        """Match a slash command to a navig CLI string.  Returns None if no match."""
        import shlex

        parts = text.strip().split(None, 1)
        if not parts:
            return None
        cmd = parts[0].lower()
        if cmd.startswith("/") and "@" in cmd:
            cmd = cmd.split("@", 1)[0]
        args = parts[1] if len(parts) > 1 else ""

        for entry in _iter_unique_registry():
            if entry.cli_template and f"/{entry.command}" == cmd:
                template = entry.cli_template
                if "{args}" in template:
                    if not args:
                        return template.replace(" {args}", "").replace("{args}", "")
                    return template.replace("{args}", shlex.quote(args))
                return template
        return None

    def _build_slash_handlers(
        self,
        chat_id: int,
        user_id: int,
        username: str,
        metadata: Any,
        is_group: bool,
    ) -> dict[str, Any]:
        """Build the slash-command dispatch table from _SLASH_REGISTRY.

        Handler entries in the registry map to methods via ``entry.handler``.
        Parameterised commands (/mode, /trace, /restart, /skill) and tier commands
        (/big, /small, /coder, /auto) are handled separately in TelegramChannel and
        are deliberately absent from this dict.
        """
        import inspect

        _ctx = {
            "chat_id": chat_id,
            "user_id": user_id,
            "username": username,
            "metadata": metadata,
            "is_group": is_group,
        }
        result: dict[str, Any] = {}
        for entry in _SLASH_REGISTRY:
            if not entry.handler:
                continue
            method = getattr(self, entry.handler, None)
            if method is None:
                continue
            try:
                sig = inspect.signature(method)
                kwargs = {k: v for k, v in _ctx.items() if k in sig.parameters}
            except (ValueError, TypeError):
                kwargs = {"chat_id": chat_id}
            cmd_key = f"/{entry.command}"
            result[cmd_key] = lambda m=method, kw=kwargs: m(**kw)
        return result

    @rate_limited
    @error_handled
    @typing_context
    async def _handle_cli_command(
        self,
        chat_id: int,
        user_id: int,
        metadata: MessageMetadata,
        navig_cmd: str,
    ) -> None:
        """Execute a navig CLI command with typing indicator and send output.

        Failure handling: if on_message() returns output containing a known SSH/
        connection error pattern the response is routed through the auto-heal
        pipeline (AutoHealMixin._heal_failure) instead of being printed raw.
        """
        if self.on_message:
            try:
                response = await self.on_message(
                    channel="telegram",
                    user_id=str(user_id),
                    message=f"navig {navig_cmd}",
                    metadata=metadata,
                )
            except Exception as _exc:
                # Unexpected exception from the routing layer - treat as UNKNOWN
                response = f"Command exited with code: 255\n{_exc}"

            if response:
                # -- Auto-Heal intercept -------------------------------------
                # Import lazily so the autoheal module isn't required at
                # startup; the whole heal pipeline is optional.
                _heal_ctx = None
                try:
                    from navig.gateway.channels.telegram_autoheal import (
                        detect_failure_in_response,
                    )

                    _heal_ctx = detect_failure_in_response(response, navig_cmd)
                except Exception:
                    pass  # never let heal detection crash the normal flow

                if _heal_ctx is not None and hasattr(self, "_heal_failure"):
                    # Fill in the chat/user IDs that the detector can't know
                    _heal_ctx.chat_id = chat_id
                    _heal_ctx.user_id = user_id
                    try:
                        await self._heal_failure(_heal_ctx)
                    except Exception:
                        # Safety net: if the heal pipeline itself crashes,
                        # fall back to showing the raw (but truncated) error.
                        import logging as _log

                        _log.getLogger(__name__).exception(
                            "autoheal: _heal_failure raised unexpectedly"
                        )
                        _raw_fallback = response[:3950]
                        _rendered = (
                            self._md_to_html(_raw_fallback)
                            if hasattr(self, "_md_to_html")
                            else _raw_fallback
                        )
                        await self.send_message(chat_id, _rendered, parse_mode="HTML")
                    return
                # -- Normal path ---------------------------------------------
                if len(response) > 4000:
                    response = response[:3950] + "\n-(truncated)"

                # -- Natural Language Formatting -----------------------------
                try:
                    prompt = (
                        f"I just executed the server command '{navig_cmd}' and got this raw output:\n"
                        f"{response}\n\n"
                        "Please summarize this naturally and concisely in 1-2 sentences. "
                        "Talk like a helpful friend. Do not regurgitate the raw output block, just tell me what was achieved or what the status is."
                    )
                    nl_response = await self.on_message(
                        channel="telegram",
                        user_id=str(user_id),
                        message=prompt,
                        metadata=metadata,
                    )
                    if nl_response and not nl_response.startswith("Command exited with code"):
                        response = nl_response
                except Exception as _nl_err:
                    import logging as _log

                    _log.getLogger(__name__).warning(
                        "NLP formatting failed for cli command: %s", _nl_err
                    )

                if (
                    navig_cmd.strip().startswith("host use")
                    and self._is_cli_command_success(response)
                    and self._has_host_connectivity_confirmation(response)
                ):
                    self._mark_chat_onboarding_step("first-host")

                rendered_response = (
                    self._md_to_html(response) if hasattr(self, "_md_to_html") else response
                )
                await self.send_message(chat_id, rendered_response, parse_mode="HTML")
            else:
                await self.send_message(chat_id, "-no output.", parse_mode=None)
        else:
            await self.send_message(chat_id, "-gateway not connected.", parse_mode=None)

    # -- Bot registration ------------------------------------------------------

    async def _register_commands(self) -> None:
        """Register slash commands with Telegram via setMyCommands API.

        The command list is derived from :data:`_SLASH_REGISTRY` -
        no separate list to keep in sync.
        """
        commands = TelegramCommandsMixin._build_command_list_for_registration()
        # Deck command is opt-in: only added to the bot's command list when
        # telegram.deck_url is configured.  Users without a Deck deployment
        # never see the /deck command in the "/" popup.
        deck_url = self._get_deck_url()
        if deck_url and not any(c.get("command") == "deck" for c in commands):
            commands.append({"command": "deck", "description": "Open the command deck"})
        result = await self._api_call("setMyCommands", {"commands": commands})
        if result is not None:
            logger.info("Registered %d bot commands with Telegram", len(commands))
        else:
            logger.warning("Failed to register bot commands")

        # Use the already-resolved deck_url (no second config read)
        if deck_url:
            await self._api_call(
                "setChatMenuButton",
                {
                    "menu_button": {
                        "type": "web_app",
                        "text": "- Deck",
                        "web_app": {"url": deck_url},
                    },
                },
            )
            logger.info("Registered Deck menu button: %s", deck_url)

    def _get_deck_url(self) -> str | None:
        """Resolve the Deck WebApp URL from config."""
        import yaml

        for cfg_path in [
            os.path.join(".navig", "config.yaml"),
            str(global_config_path()),
        ]:
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path) as f:
                        cfg = yaml.safe_load(f) or {}
                    url = (cfg.get("telegram", {}) or {}).get("deck_url")
                    if url:
                        return url
                except Exception as e:
                    logger.debug("Could not read deck_url from config %s: %s", cfg_path, e)
        return None

    # ── Bot Identity & Auto-Reply Features ─────────────────────────────────

    async def _handle_about(self, chat_id: int) -> None:
        """Learn about NAVIG."""
        msg = (
            "🧭 <b>NAVIG</b>\n\n"
            "Operational intelligence layer for your infrastructure.\n\n"
            "Connects SSH hosts, databases, Docker containers, and AI models "
            "through a unified command surface — CLI, Telegram bot, and MCP server.\n\n"
            "Use <code>/help</code> to explore all capabilities."
        )
        await self.send_message(chat_id, msg)

    async def _handle_auto_start(self, chat_id: int, user_id: int, text: str) -> None:
        """Start AI conversational auto-reply using durable runtime state.

        Note: For persona-only changes without enabling auto-reply, prefer /persona <name>.
        """
        role = text[len("/auto_start") :].strip() or "assistant"
        try:
            from navig.store.runtime import get_runtime_store

            get_runtime_store().set_ai_state(
                user_id=user_id,
                chat_id=chat_id,
                mode="active",
                persona=role,
                context={"source": "telegram", "command": "auto_start"},
            )
            # Also update the unified persona system (best-effort)
            try:
                from navig.personas.manager import switch_persona

                await switch_persona(
                    name=role,
                    user_id=user_id,
                    chat_id=chat_id,
                    deliver_assets=False,
                )
            except Exception as _pe:
                logger.debug("Persona sync during auto_start skipped: %s", _pe)

            await self.send_message(
                chat_id,
                f"✅ Auto-replies <b>ACTIVATED</b> with persona: <code>{role}</code>\n"
                f"<i>Tip: use <code>/persona {role}</code> to switch personas without enabling auto-reply.</i>",
            )
        except Exception as e:
            logger.error("Failed to start auto-reply state: %s", e)
            await self.send_message(chat_id, "❌ Failed to activate auto-replies.")

    async def _handle_auto_stop(self, chat_id: int, user_id: int) -> None:
        """Stop AI conversational auto-reply."""
        try:
            from navig.store.runtime import get_runtime_store

            get_runtime_store().clear_ai_state(user_id)
            await self.send_message(chat_id, "🛑 Auto-replies deactivated.")
        except Exception as e:
            logger.error("Failed to stop auto-reply state: %s", e)
            await self.send_message(chat_id, "🛑 Auto-replies halted.")

    async def _handle_auto_status(self, chat_id: int, user_id: int) -> None:
        """Check current auto-reply status."""
        state = None
        try:
            from navig.core.continuation import policy_from_context
            from navig.store.runtime import get_runtime_store

            state = get_runtime_store().get_ai_state(user_id)
        except Exception as e:
            logger.error("Failed to read auto-reply state: %s", e)

        if state and state.get("mode") == "active":
            role = state.get("persona") or "assistant"
            context = state.get("context") or {}
            policy = policy_from_context(context)
            continuation_meta = (
                (context.get("continuation") or {}) if isinstance(context, dict) else {}
            )
            cls_state = continuation_meta.get("last_classifier_state")
            cls_reason = continuation_meta.get("last_classifier_reason")
            busy_until = continuation_meta.get("busy_until")
            busy_reason = continuation_meta.get("busy_reason")
            last_skip_reason = continuation_meta.get("last_skip_reason")
            cont = (
                f"profile={policy.profile}, enabled={policy.enabled}, paused={policy.paused}, "
                f"skip_next={policy.skip_next}, turns={policy.turns_used}/{policy.max_turns}, "
                f"cooldown={policy.cooldown_seconds}s"
            )
            if cls_state:
                cont += f", classifier={cls_state}"
                if cls_reason:
                    cont += f" ({cls_reason})"
            if busy_until:
                cont += f", busy_until={busy_until}"
                if busy_reason:
                    cont += f" ({busy_reason})"
            if last_skip_reason:
                cont += f", last_skip={last_skip_reason}"
            await self.send_message(
                chat_id, f"✅ AI is currently <b>ACTIVE</b> in <code>{role}</code> mode.\nContinuation: <code>{cont}</code>"
            )
            return

        await self.send_message(chat_id, "🛑 AI auto-reply is currently <b>INACTIVE</b>.")

    async def _handle_continue(self, chat_id: int, user_id: int, text: str = "") -> None:
        """Enable continuation policy with optional profile and space focus.

        Syntax:
          /continue
          /continue <space>
          /continue <profile>
          /continue <profile> <space>
        Profiles: conservative, balanced, aggressive
        """
        try:
            from navig.core.continuation import (
                decision_sensitivity_for_profile,
                merge_policy,
                normalize_profile_name,
                policy_from_context,
                suppression_windows_for_profile,
            )
            from navig.spaces import normalize_space_name
            from navig.store.runtime import get_runtime_store

            store = get_runtime_store()
            state = store.get_ai_state(user_id) or {}
            if state.get("mode") != "active":
                await self.send_message(
                    chat_id,
                    "Start auto mode first with `/auto_start <persona>`. ",
                )
                return

            raw_args = text[len("/continue") :].strip().split() if text else []
            profile = "conservative"
            preferred_space = ""

            if raw_args:
                head = raw_args[0].strip().lower()
                if head in {"conservative", "balanced", "aggressive"}:
                    profile = normalize_profile_name(head)
                    if len(raw_args) > 1:
                        preferred_space = normalize_space_name(" ".join(raw_args[1:]))
                else:
                    preferred_space = normalize_space_name(" ".join(raw_args))

            context = merge_policy(
                state.get("context") or {},
                profile=profile,
                enabled=True,
                paused=False,
                skip_next=False,
                cooldown_seconds=None,
                max_turns=None,
            )
            if preferred_space:
                context["continuation"] = {
                    **(context.get("continuation") or {}),
                    "space": preferred_space,
                }

            policy = policy_from_context(context)
            windows = suppression_windows_for_profile(policy.profile)
            sensitivity = decision_sensitivity_for_profile(policy.profile)

            store.set_ai_state(
                user_id=user_id,
                chat_id=chat_id,
                mode="active",
                persona=state.get("persona") or "assistant",
                context=context,
            )
            await self.send_message(
                chat_id,
                f"▶️ Autonomous continuation <b>enabled</b> (profile <code>{profile}</code>)."
                + f"\nPolicy: cooldown={policy.cooldown_seconds}s, max_turns={policy.max_turns}, "
                + f"suppression(wait={windows.get('wait', 0)}s, blocked={windows.get('blocked', 0)}s), "
                + f"decision={sensitivity}"
                + (f"\nSpace focus: <code>{preferred_space}</code>" if preferred_space else ""),
            )
        except Exception as e:
            logger.error("Failed to enable continuation: %s", e)
            await self.send_message(chat_id, "❌ Failed to enable continuation.")

    async def _handle_pause(self, chat_id: int, user_id: int) -> None:
        """Pause continuation policy without disabling auto mode."""
        try:
            from navig.core.continuation import merge_policy
            from navig.store.runtime import get_runtime_store

            store = get_runtime_store()
            state = store.get_ai_state(user_id) or {}
            context = merge_policy(state.get("context") or {}, paused=True)
            store.set_ai_state(
                user_id=user_id,
                chat_id=chat_id,
                mode=state.get("mode") or "inactive",
                persona=state.get("persona") or "assistant",
                context=context,
            )
            await self.send_message(chat_id, "⏸️ Continuation paused. Use <code>/continue</code> to resume.")
        except Exception as e:
            logger.error("Failed to pause continuation: %s", e)
            await self.send_message(chat_id, "❌ Failed to pause continuation.")

    async def _handle_skip(self, chat_id: int, user_id: int) -> None:
        """Skip the next continuation trigger."""
        try:
            from navig.core.continuation import merge_policy
            from navig.store.runtime import get_runtime_store

            store = get_runtime_store()
            state = store.get_ai_state(user_id) or {}
            context = merge_policy(state.get("context") or {}, skip_next=True)
            store.set_ai_state(
                user_id=user_id,
                chat_id=chat_id,
                mode=state.get("mode") or "inactive",
                persona=state.get("persona") or "assistant",
                context=context,
            )
            await self.send_message(chat_id, "⏭️ Next continuation turn will be skipped.")
        except Exception as e:
            logger.error("Failed to set skip-next continuation: %s", e)
            await self.send_message(chat_id, "❌ Failed to set skip-next continuation.")

    # ── Persona commands ──────────────────────────────────────────────────────

    async def _handle_persona(self, chat_id: int, user_id: int, text: str = "") -> None:
        """Route /persona subcommands: list, info, reset, or switch."""
        arg = text[len("/persona") :].strip()
        if not arg or arg == "list":
            await self._handle_personas(chat_id=chat_id, user_id=user_id)
            return
        if arg == "info":
            await self._handle_persona_info(chat_id, user_id)
            return
        if arg == "reset":
            await self._handle_persona_reset(chat_id, user_id)
            return
        # Treat anything else as a persona name to switch to
        await self._handle_persona_switch(chat_id, user_id, arg)

    async def _handle_personas(self, chat_id: int, user_id: int = 0) -> None:
        """List all available personas, showing the currently active one."""
        try:
            from navig.personas.manager import list_personas
            from navig.personas.renderer import render_persona_list
            from navig.personas.store import get_active_persona

            personas = list_personas()
            active_name = get_active_persona(user_id, chat_id)
            text = render_persona_list(personas, active_name)
            await self.send_message(chat_id, text)
        except Exception as e:
            logger.error("_handle_personas error: %s", e)
            # Graceful fallback using builtin list
            try:
                from navig.personas.contracts import BUILTIN_PERSONAS

                roles = "\n".join(f"• <code>{p}</code>" for p in BUILTIN_PERSONAS)
            except Exception:
                roles = "• <code>default</code>\n• <code>assistant</code>\n• <code>tyler</code>\n• <code>storyteller</code>\n• <code>philosopher</code>\n• <code>teacher</code>"
            await self.send_message(
                chat_id,
                f"🎭 <b>Available AI Personas:</b>\n\n{roles}\n\nUse <code>/persona &lt;name&gt;</code> to switch.",
            )

    async def _handle_persona_switch(self, chat_id: int, user_id: int, name: str) -> None:
        """Switch the active AI persona."""
        await self.send_typing(chat_id)
        try:
            from navig.personas.manager import switch_persona
            from navig.personas.renderer import render_switch_confirmation

            config = await switch_persona(
                name=name,
                user_id=user_id,
                chat_id=chat_id,
                deliver_assets=True,
                bot_client=self,
            )
            msg = render_switch_confirmation(config)
            await self.send_message(chat_id, msg)
        except Exception as e:
            logger.error("Failed to switch persona to %r: %s", name, e)
            await self.send_message(
                chat_id,
                f"❌ Could not switch to persona <b>{html.escape(name)}</b>: {html.escape(str(e))}",
                parse_mode="HTML",
            )

    async def _handle_persona_info(self, chat_id: int, user_id: int) -> None:
        """Show details about the currently active persona."""
        try:
            from navig.personas.loader import load_persona
            from navig.personas.manager import get_active_persona_config
            from navig.personas.renderer import render_persona_info

            config = await get_active_persona_config(user_id, chat_id)
            _, soul_text = load_persona(config.name)
            msg = render_persona_info(config, soul_text)
            await self.send_message(chat_id, msg)
        except Exception as e:
            logger.error("_handle_persona_info error: %s", e)
            await self.send_message(chat_id, "❌ Could not load persona info.", parse_mode=None)

    async def _handle_persona_reset(self, chat_id: int, user_id: int) -> None:
        """Reset persona to the built-in default."""
        await self._handle_persona_switch(chat_id, user_id, "default")

    async def _handle_explain_ai(self, chat_id: int, user_id: int = 0, text: str = "") -> None:
        topic = (
            text[len("/explain_ai") :].strip()
            if text.lower().startswith("/explain_ai")
            else text.strip()
        )
        if not topic:
            await self.send_message(
                chat_id,
                "Please provide a topic. Example: `/explain_ai quantum computing`",
            )
            return

        await self.send_typing(chat_id)
        try:
            prompt = (
                "Explain the following topic clearly and comprehensively, "
                "structured for a Telegram message:\n\nTopic: " + topic
            )
            # Route through the standard LLM path (same as /think)
            if hasattr(self, "on_message"):
                await self.on_message(
                    chat_id,
                    user_id or chat_id,
                    prompt,
                    system_override="You are an expert explainer.",
                )
            else:
                # Minimal fallback: call llm_router directly
                from navig.llm_router import get_llm_router

                lr = get_llm_router()
                if lr is None:
                    raise RuntimeError("LLM router not initialised")
                result = await lr.route(prompt, tier="small")
                await self.send_message(chat_id, str(result))
        except Exception as e:  # noqa: BLE001
            await self.send_message(chat_id, f"❌ Failed to explain: {e}", parse_mode=None)

    async def _handle_music(self, chat_id: int) -> None:
        await self.send_message(
            chat_id,
            "🎵 <b>Music Conversion</b>\n\nIntegration with Spotify/Apple Music APIs pending migration.",
            parse_mode="HTML",
        )

    async def _handle_imagegen(self, chat_id: int, text: str) -> None:
        prompt = text[len("/imagegen") :].strip()
        if not prompt:
            await self.send_message(
                chat_id,
                "Please provide a prompt. Example: `/imagegen cybernetic city sunset`",
                parse_mode="HTML",
            )
            return
        await self.send_typing(chat_id)
        await self.send_message(
            chat_id,
            "🎨 Image generation model not yet bridged to the current workspace.",
            parse_mode=None,
        )

    async def _handle_profile(self, chat_id: int, user_id: int, username: str) -> None:
        await self._handle_user(chat_id, user_id, username)

    async def _handle_quote(self, chat_id: int) -> None:
        await self.send_message(
            chat_id,
            "💬 Quote storage is currently being initialized in the new database.",
            parse_mode=None,
        )

    async def _handle_respect(self, chat_id: int) -> None:
        await self.send_message(chat_id, "✊ Respect system ledger is syncing...", parse_mode=None)

    async def _handle_currency(self, chat_id: int, text: str) -> None:
        await self.send_message(
            chat_id, "💹 Real-time fiat exchange rates are offline.", parse_mode=None
        )

    async def _handle_crypto_list(self, chat_id: int) -> None:
        await self.send_message(
            chat_id,
            "🪙 <b>Crypto Reference:</b>\n• BTC - Bitcoin\n• ETH - Ethereum\n• SOL - Solana\n\n(Price feed offline)",
            parse_mode="HTML",
        )

    @staticmethod
    def _sniff_reminder_intent(text: str) -> str | None:
        """Detect a reminder request in any language; return a normalised /remindme string or None.

        Supports: English, Russian, French, German, Spanish, Chinese, Korean.
        Pattern matching is script/script-mixing-agnostic so it works even when
        the verb and message are in different Unicode blocks.
        """
        lowered = (text or "").lower()
        _VERBS = (
            "remind me", "set reminder", "set a reminder",
            "напомни", "напомните",          # Russian
            "rappelle-moi", "rappelle moi",   # French
            "erinnere mich",                  # German
            "recuérdame",                     # Spanish
            "提醒我",                          # Chinese
            "알려줘",                           # Korean
        )
        if not any(v in lowered for v in _VERBS):
            return None

        # ── Absolute time HH:MM ────────────────────────────────────────────
        abs_m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
        if abs_m:
            hour, minute = int(abs_m.group(1)), int(abs_m.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                msg = (text[: abs_m.start()] + " " + text[abs_m.end() :]).strip()
                for v in sorted(_VERBS, key=len, reverse=True):
                    msg = re.sub(rf"\b{re.escape(v)}\b", "", msg, flags=re.IGNORECASE)
                msg = re.sub(
                    r"\b(мне|at|в|um|à|me|mir|to|for|in)\b", "", msg, flags=re.IGNORECASE
                )
                msg = re.sub(r"\s+", " ", msg).strip(" .,!?:;-–—")
                return f"/remindme at {hour:02d}:{minute:02d} {msg or 'reminder'}"

        # ── Relative time N min / hour / day ──────────────────────────────
        rel_m = re.search(
            r"\b(\d+)\s*(мин(?:уты?|ут?)?|min(?:utes?)?|час(?:а|ов)?|hours?|hr?s?|day?s?)\b",
            text,
            flags=re.IGNORECASE,
        )
        if rel_m:
            qty = int(rel_m.group(1))
            unit_raw = rel_m.group(2).lower()
            if unit_raw.startswith(("ч", "h")):
                unit_word = "hour" if qty == 1 else "hours"
            elif unit_raw.startswith("d"):
                unit_word = "day" if qty == 1 else "days"
            else:
                unit_word = "minute" if qty == 1 else "minutes"
            msg = (text[: rel_m.start()] + " " + text[rel_m.end() :]).strip()
            for v in sorted(_VERBS, key=len, reverse=True):
                msg = re.sub(rf"\b{re.escape(v)}\b", "", msg, flags=re.IGNORECASE)
            msg = re.sub(
                r"\b(мне|через|in|nach|me|mir|to|for)\b", "", msg, flags=re.IGNORECASE
            )
            msg = re.sub(r"\s+", " ", msg).strip(" .,!?:;-–—")
            return f"/remindme in {qty} {unit_word} {msg or 'reminder'}"

        return None

    def _parse_remindme_request(self, text: str) -> tuple[datetime | None, str, str | None]:
        """Parse /remindme payload to (remind_at_local→UTC, message, error)."""
        arg = text[len("/remindme") :].strip()
        if not arg:
            return (
                None,
                "",
                "Usage: <code>/remindme in 30 minutes check logs</code> or <code>/remindme at 21:30 deploy review</code>",
            )

        rel_match = re.match(
            r"^(?:in\s+)?(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\s+(?:to\s+)?(.+)$",
            arg,
            flags=re.IGNORECASE,
        )
        if rel_match:
            qty = int(rel_match.group(1))
            unit = rel_match.group(2).lower()
            msg = rel_match.group(3).strip()
            if not msg:
                return None, "", "Reminder message cannot be empty."
            now = datetime.now(timezone.utc)
            if unit.startswith("m"):
                remind_at = now + timedelta(minutes=qty)
            elif unit.startswith("h"):
                remind_at = now + timedelta(hours=qty)
            else:
                remind_at = now + timedelta(days=qty)
            return remind_at, msg, None

        abs_match = re.match(r"^at\s+(\d{1,2}):(\d{2})\s+(.+)$", arg, flags=re.IGNORECASE)
        if abs_match:
            hour = int(abs_match.group(1))
            minute = int(abs_match.group(2))
            msg = abs_match.group(3).strip()
            if hour > 23 or minute > 59:
                return None, "", "Time must be in 24h format, e.g. <code>at 21:30</code>."
            if not msg:
                return None, "", "Reminder message cannot be empty."
            # Build time in server-local timezone, then convert to UTC for DB storage
            now_local = datetime.now().astimezone()
            remind_at = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if remind_at <= now_local:
                remind_at += timedelta(days=1)
            remind_at = remind_at.astimezone(timezone.utc)
            return remind_at, msg, None

        return (
            None,
            "",
            "Could not parse reminder. Use <code>/remindme in 10 minutes &lt;message&gt;</code> or <code>/remindme at 18:45 &lt;message&gt;</code>.",
        )

    async def _handle_remindme(self, chat_id: int, user_id: int, text: str) -> None:
        remind_at, message, err = self._parse_remindme_request(text)
        if err or remind_at is None:
            await self.send_message(chat_id, err or "Invalid reminder format.")
            return

        from navig.store.runtime import get_runtime_store

        reminder_id = get_runtime_store().create_reminder(
            user_id=user_id,
            chat_id=chat_id,
            message=message,
            remind_at=remind_at,
        )
        # Show the time in server-local timezone so the user sees what they typed
        local_when = remind_at.astimezone().strftime("%Y-%m-%d %H:%M")
        await self.send_message(
            chat_id,
            f"⏰ Reminder set.\nID: <code>{reminder_id}</code>\nWhen: <code>{local_when}</code>\nMessage: {html.escape(message)}",
            parse_mode="HTML",
        )

    async def _handle_myreminders(self, chat_id: int, user_id: int, metadata: MessageMetadata | None = None) -> None:
        from navig.store.runtime import get_runtime_store

        reminders = get_runtime_store().get_user_reminders(user_id)
        if not reminders:
            await self.send_message(
                chat_id,
                "📭 You have no active reminders.",
                parse_mode=None,
            )
            return

        lines = ["⏰ <b>Your Active Reminders</b>", ""]
        for row in reminders:
            rid = row.get("id")
            remind_at_raw = str(row.get("remind_at") or "")
            # Convert UTC DB timestamp to server-local time so it matches what the user typed.
            try:
                from datetime import timezone as _tz
                _due_dt = datetime.fromisoformat(remind_at_raw.rstrip("Z")).replace(tzinfo=_tz.utc)
                remind_at = _due_dt.astimezone().strftime("%Y-%m-%d %H:%M")
            except Exception:
                remind_at = remind_at_raw.replace("T", " ")[:16]
            msg = html.escape(str(row.get("message") or "").strip())
            lines.append(f"<code>#{rid}</code> — <code>{remind_at}</code>\n  {msg}")
        lines.append("\n<i>Use /cancelreminder &lt;id&gt; to remove one.</i>")
        keyboard = [
            [
                {"text": "🔄 Refresh", "callback_data": "slash:reminders"},
                {"text": "➕ Add reminder", "callback_data": "slash:remindme"},
            ]
        ]
        await self.send_message(
            chat_id,
            "\n".join(lines),
            parse_mode="HTML",
            keyboard=keyboard,
        )

    async def _handle_cancelreminder(self, chat_id: int, user_id: int, text: str) -> None:
        arg = text[len("/cancelreminder") :].strip()
        if not arg:
            await self.send_message(
                chat_id,
                "Usage: <code>/cancelreminder &lt;id&gt;</code> or <code>/cancelreminder all</code>",
                parse_mode="HTML",
            )
            return

        from navig.store.runtime import get_runtime_store

        store = get_runtime_store()

        if arg.lower() == "all":
            reminders = store.get_user_reminders(user_id) or []
            if not reminders:
                await self.send_message(chat_id, "No active reminders to cancel.", parse_mode=None)
                return
            for r in reminders:
                rid = r.get("id") if isinstance(r, dict) else getattr(r, "id", None)
                if rid is not None:
                    store.cancel_reminder(rid, user_id)
            await self.send_message(
                chat_id,
                f"✅ Cancelled {len(reminders)} reminder{'s' if len(reminders) != 1 else ''}.",
                parse_mode=None,
            )
            return

        if not arg.isdigit():
            await self.send_message(
                chat_id,
                "Usage: <code>/cancelreminder &lt;id&gt;</code> or <code>/cancelreminder all</code>",
                parse_mode="HTML",
            )
            return

        reminder_id = int(arg)
        deleted = store.cancel_reminder(reminder_id, user_id)
        if deleted:
            await self.send_message(chat_id, f"✅ Reminder <code>{reminder_id}</code> cancelled.")
        else:
            await self.send_message(
                chat_id,
                f"❌ No active reminder found for id <code>{reminder_id}</code>.",
                parse_mode=None,
            )

    async def _handle_stats_global(self, chat_id: int) -> None:
        await self.send_message(chat_id, "📊 Global chat statistics are gathering data...")

    async def _handle_choice(self, chat_id: int, text: str) -> None:
        args = text[len("/choice") :].strip()
        if not args:
            await self.send_message(
                chat_id,
                "Usage: <code>/choice pizza or burger</code>  — also accepts <code>,</code> or <code>|</code> as separators.",
                parse_mode="HTML",
            )
            return
        import re

        # Normalise separators: | and , become ' or '
        normalised = re.sub(r"\s*[|,]\s*", " or ", args)
        choices = [
            c.strip() for c in re.split(r"\bor\b", normalised, flags=re.IGNORECASE) if c.strip()
        ]
        if len(choices) < 2:
            await self.send_message(
                chat_id,
                "Please give me at least two options. Example: <code>/choice tea or coffee</code>",
                parse_mode="HTML",
            )
            return
        await self.send_message(chat_id, f"🎲 I choose: <b>{html.escape(random.choice(choices))}</b>", parse_mode="HTML")

    async def _handle_kick(self, chat_id: int, text: str) -> None:
        target = text[len("/kick") :].strip()
        await self.send_message(
            chat_id,
            f"👢 Core restriction: Bot requires channel Admin rights to ban <code>{html.escape(target)}</code>.",
            parse_mode="HTML",
        )

    async def _handle_mute(self, chat_id: int, text: str) -> None:
        target = text[len("/mute") :].strip()
        await self.send_message(
            chat_id,
            f"🔇 Core restriction: Bot requires channel Admin rights to restrict <code>{html.escape(target)}</code>.",
            parse_mode="HTML",
        )

    async def _handle_unmute(self, chat_id: int, text: str) -> None:
        target = text[len("/unmute") :].strip()
        await self.send_message(
            chat_id,
            f"🔊 Core restriction: Bot requires channel Admin rights to pardon <code>{html.escape(target)}</code>.",
            parse_mode="HTML",
        )

    async def _handle_search(self, chat_id: int, text: str) -> None:
        query = text[len("/search") :].strip()
        await self.send_message(chat_id, f"🔍 User search proxy offline for query: <code>{html.escape(query)}</code>", parse_mode="HTML")
