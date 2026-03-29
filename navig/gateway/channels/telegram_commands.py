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
import json
import logging
import os
import random
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from navig.platform.paths import global_config_path, msg_trace_path

logger = logging.getLogger(__name__)

from navig.gateway.channels.types import MessageMetadata
from navig.gateway.channels.utils.decorators import (
    error_handled,
    rate_limited,
    typing_context,
)

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
    """Return a single Markdown line describing Bridge Grid status (DUP-5 fix)."""
    if online:
        return f"- *Bridge Grid* `{url}` - - primary"
    return f"- *Bridge Grid* `{url}` - offline"


# -- Slash-command registry ----------------------------------------------------
# Single source of truth for every Telegram slash command.
# Drives: CLI dispatch - bot registration (setMyCommands) - /help text.
# To add a CLI-backed command: one entry here.  Done.


@dataclass
class SlashCommandEntry:
    """Metadata for a single Telegram slash command."""

    command: str  # without leading "/"
    description: str  # shown in Telegram command list and /help
    cli_template: str | None = (
        None  # navig CLI template; ``{args}`` is replaced with user input
    )
    handler: str | None = None  # method name on TelegramCommandsMixin to call directly
    visible: bool = True  # include in /help and setMyCommands
    category: str = "general"  # section heading for /help


_SLASH_REGISTRY: list[SlashCommandEntry] = [
    # --- Core ----------------------------------------------------------------
    SlashCommandEntry(
        "start", "Wake up greeting", handler="_handle_start", category="core"
    ),
    SlashCommandEntry("remindme", "Set reminders", handler="_handle_remindme", category="utilities"),
    SlashCommandEntry("myreminders", "List your active reminders", handler="_handle_myreminders", category="utilities"),
    SlashCommandEntry("cancelreminder", "Cancel a reminder", handler="_handle_cancelreminder", category="utilities"),
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
    ),
    SlashCommandEntry(
        "model",
        "Active model routing table",
        handler="_handle_models_command",
        category="core",
        visible=False,
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
    SlashCommandEntry(
        "briefing", "Today's summary", handler="_handle_briefing", category="core"
    ),
    SlashCommandEntry(
        "deck",
        "Open the command deck",
        handler="_handle_deck",
        category="core",
        visible=False,
    ),
    SlashCommandEntry(
        "ping", "Quick alive check", handler="_handle_ping", category="core"
    ),
    SlashCommandEntry(
        "skill", "Run a NAVIG skill - /skill list to browse", category="core"
    ),
    # --- Monitoring ----------------------------------------------------------
    SlashCommandEntry(
        "disk",
        "Disk usage",
        cli_template="host monitor show --disk",
        category="monitoring",
    ),
    SlashCommandEntry(
        "memory", "RAM status", cli_template='run "free -h"', category="monitoring"
    ),
    SlashCommandEntry(
        "cpu", "Load / CPU info", cli_template='run "uptime"', category="monitoring"
    ),
    SlashCommandEntry(
        "uptime", "Server uptime", cli_template='run "uptime -p"', category="monitoring"
    ),
    SlashCommandEntry(
        "services",
        "Running services",
        cli_template='run "systemctl list-units --type=service --state=running --no-pager | head -40"',
        category="monitoring",
    ),
    SlashCommandEntry(
        "ports",
        "Open ports",
        cli_template='run "ss -tlnp | head -30"',
        category="monitoring",
    ),
    SlashCommandEntry(
        "top",
        "Process list",
        cli_template='run "top -bn1 | head -20"',
        visible=False,
        category="monitoring",
    ),
    SlashCommandEntry(
        "df",
        "Disk usage (df)",
        cli_template='run "df -h"',
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
        "docker", "List containers", cli_template="docker ps", category="docker"
    ),
    SlashCommandEntry(
        "logs",
        "Container logs (+ name)",
        cli_template="docker logs {args} -n 50",
        category="docker",
    ),
    SlashCommandEntry(
        "restart", "Restart container (+ name) or daemon", category="docker"
    ),
    # --- Database ------------------------------------------------------------
    SlashCommandEntry(
        "db", "List databases", cli_template="db list", category="database"
    ),
    SlashCommandEntry(
        "tables",
        "Tables in a database (+ db name)",
        cli_template="db tables {args}",
        category="database",
    ),
    # --- Tools ---------------------------------------------------------------
    SlashCommandEntry(
        "hosts", "Configured servers", cli_template="host list", category="tools"
    ),
    SlashCommandEntry(
        "use",
        "Switch active host (+ name)",
        cli_template="host use {args}",
        category="tools",
    ),
    SlashCommandEntry(
        "run", "Execute remote command", cli_template='run "{args}"', category="tools"
    ),
    SlashCommandEntry(
        "backup", "Backup status", cli_template="backup show", category="tools"
    ),
    # --- Formatting & Reasoning --------------------------------------------------
    SlashCommandEntry(
        "format",
        "Convert Markdown to Telegram-friendly format",
        handler="_handle_format",
        category="tools",
    ),
    SlashCommandEntry(
        "fmt",
        "Format shorthand (alias for /format)",
        handler="_handle_format",
        category="tools",
        visible=False,
    ),
    SlashCommandEntry(
        "think", "Reason through a topic — paginated cards", category="tools"
    ),
    SlashCommandEntry(
        "refine", "Sharpen your idea with AI clarification", category="tools"
    ),
    # --- Utilities -----------------------------------------------------------
    SlashCommandEntry(
        "ip",
        "Server public IP",
        cli_template='run "curl -s ifconfig.me"',
        category="utilities",
    ),
    SlashCommandEntry(
        "time", "Server time", cli_template='run "date"', category="utilities"
    ),
    SlashCommandEntry(
        "weather",
        "Weather report",
        cli_template="run \"curl -s 'wttr.in/?format=3'\"",
        category="utilities",
    ),
    SlashCommandEntry(
        "dns",
        "DNS lookup (+ domain)",
        cli_template='run "dig +short {args}"',
        category="utilities",
    ),
    SlashCommandEntry(
        "ssl",
        "SSL cert check (+ domain)",
        cli_template="run \"echo | openssl s_client -connect {args}:443 -servername {args} 2>/dev/null | openssl x509 -noout -dates 2>/dev/null || echo 'no cert found'\"",
        category="utilities",
    ),
    SlashCommandEntry(
        "whois",
        "Domain whois (+ domain)",
        cli_template='run "whois {args} | head -30"',
        category="utilities",
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
        "settings",
        "Main config hub - audio, providers, focus, model",
        handler="_handle_settings_menu",
        category="model",
    ),
    SlashCommandEntry(
        "providers", "AI Provider Hub", handler="_handle_providers", category="model"
    ),
    SlashCommandEntry(
        "provider",
        "AI Provider Hub (alias)",
        handler="_handle_providers",
        category="model",
        visible=False,
    ),
    SlashCommandEntry(
        "mode", "Set focus mode (work, deep-focus, etc.)", category="model"
    ),
    SlashCommandEntry("big", "Force big model for next message", category="model"),
    SlashCommandEntry("small", "Force small model for next message", category="model"),
    SlashCommandEntry("coder", "Force coder model for next message", category="model"),
    SlashCommandEntry("auto", "Reset to automatic model selection", category="model"),
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
    SlashCommandEntry("voiceon", "Enable voice input (STT)", category="voice"),
    SlashCommandEntry("voiceoff", "Disable voice input (STT)", category="voice"),
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
        "debug",
        "Package paths, vault, flags",
        handler="_handle_debug",
        category="diagnostics",
    ),
    SlashCommandEntry("trace", "Recent conversation history", category="diagnostics"),
    SlashCommandEntry(
        "autoheal",
        "Auto-Heal - /autoheal on|off|status|hive on|hive off",
        category="diagnostics",
    ),
    # --- Digital Ghost / Laravel Port ---
    SlashCommandEntry(
        "about", "Learn about the bot", handler="_handle_about", category="core"
    ),
    SlashCommandEntry(
        "auto_start",
        "Enable AI auto-replies",
        handler="_handle_auto_start",
        category="ai",
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
        "auto_roles",
        "List available AI personalities",
        handler="_handle_auto_roles",
        category="ai",
    ),
    SlashCommandEntry(
        "explain_ai",
        "AI explains any topic",
        handler="_handle_explain_ai",
        category="ai",
    ),
    SlashCommandEntry(
        "music", "Convert music links (beta)", handler="_handle_music", category="media"
    ),
    SlashCommandEntry(
        "imagegen",
        "Generate AI images (beta)",
        handler="_handle_imagegen",
        category="media",
    ),
    SlashCommandEntry(
        "profile", "View user profiles", handler="_handle_profile", category="social"
    ),
    SlashCommandEntry(
        "quote",
        "Save and view quotes (beta)",
        handler="_handle_quote",
        category="social",
    ),
    SlashCommandEntry(
        "respect",
        "Show respect to users (beta)",
        handler="_handle_respect",
        category="social",
    ),
    SlashCommandEntry(
        "currency",
        "Convert currency (beta)",
        handler="_handle_currency",
        category="utilities",
    ),
    SlashCommandEntry(
        "crypto_list",
        "List cryptocurrencies (beta)",
        handler="_handle_crypto_list",
        category="utilities",
    ),
    SlashCommandEntry(
        "remindme",
        "Set reminders",
        handler="_handle_remindme",
        category="utilities",
    ),
    SlashCommandEntry(
        "myreminders",
        "List your active reminders",
        handler="_handle_myreminders",
        category="utilities",
    ),
    SlashCommandEntry(
        "cancelreminder",
        "Cancel a reminder",
        handler="_handle_cancelreminder",
        category="utilities",
    ),
    SlashCommandEntry(
        "stats_global",
        "Chat activity statistics (beta)",
        handler="_handle_stats_global",
        category="utilities",
    ),
    SlashCommandEntry(
        "choice", "Make random choices", handler="_handle_choice", category="utilities"
    ),
    SlashCommandEntry(
        "kick", "Remove user from chat", handler="_handle_kick", category="admin"
    ),
    SlashCommandEntry(
        "mute", "Silence user temporarily", handler="_handle_mute", category="admin"
    ),
    SlashCommandEntry(
        "unmute", "Restore user voice", handler="_handle_unmute", category="admin"
    ),
    SlashCommandEntry(
        "search", "Find users", handler="_handle_search", category="admin"
    ),
]


class TelegramCommandsMixin:
    """Mixin for TelegramChannel - all slash-command handler methods."""

    # -- Core slash handlers ---------------------------------------------------

    async def _handle_start(self, chat_id: int, username: str) -> None:
        """Greeting on /start - warm, natural, time-of-day aware."""
        hour = datetime.now().hour
        name = username if username and username != "None" else "hey"
        if 5 <= hour < 8:
            greeting = random.choice(
                [
                    f"morning, {name}. you're up early - what's going on?",
                    f"hey {name}, early start today. what's up?",
                ]
            )
        elif 8 <= hour < 12:
            greeting = random.choice(
                [
                    f"hey {name}! what are we working on?",
                    "morning. what do you need?",
                ]
            )
        elif 12 <= hour < 18:
            greeting = random.choice(
                [
                    "hey! what's on your mind?",
                    f"yo {name}, what can I do for you?",
                ]
            )
        elif 18 <= hour < 22:
            greeting = random.choice(
                [
                    f"hey {name}. still at it?",
                    "evening. what do you need?",
                ]
            )
        else:
            greeting = random.choice(
                [
                    "late one, huh? what's up?",
                    f"hey {name}. burning the midnight oil?",
                ]
            )
        await self.send_message(chat_id, greeting, parse_mode=None)

    async def _handle_help(self, chat_id: int) -> None:
        """Command reference (/help) - auto-generated from _SLASH_REGISTRY."""
        _cat_labels: dict[str, str] = {
            "core": "- *core*",
            "monitoring": "- *monitoring*",
            "docker": "- *docker*",
            "database": "- *database*",
            "tools": "- *tools*",
            "utilities": "- *utilities*",
            "model": "- *model control*",
            "voice": "- *voice & AI settings*",
            "diagnostics": "- *diagnostics & healing*",
            "ai": "- *AI features*",
            "media": "- *media tools*",
            "social": "- *social*",
            "admin": "- *business chats / admin*",
        }
        seen: set[str] = set()
        lines = ["*things I respond to:*"]
        for entry in _SLASH_REGISTRY:
            if not entry.visible:
                continue
            if entry.category not in seen:
                seen.add(entry.category)
                lines.append("\n" + _cat_labels.get(entry.category, entry.category))
            lines.append(f"/{entry.command} - {entry.description}")
        # Deck: show only when configured, otherwise explain how to enable
        deck_url = self._get_deck_url()
        if deck_url:
            lines.append("/deck - Open the command deck")
        else:
            lines.append(
                "\n_Deck UI is disabled. To activate: set `telegram.deck_url` in `~/.navig/config.yaml` and point it at your NAVIG Deck instance._"
            )
        lines.append("\n-or just talk. I understand.")
        await self.send_message(chat_id, "\n".join(lines))

    async def _handle_ping(self, chat_id: int) -> None:
        """Quick alive check (/ping)."""
        await self.send_message(chat_id, "pong.", parse_mode=None)

    async def _handle_status(self, chat_id: int, user_id: int = 0) -> None:
        """Space-aware status summary for Telegram users (/status)."""
        from navig.spaces import get_default_space
        from navig.spaces.progress import (
            collect_spaces_progress,
            format_spaces_progress_lines,
        )

        selected_space = get_default_space()
        rows = collect_spaces_progress()

        lines = ["*NAVIG Status*", "", f"Default space: `{selected_space}`"]

        if rows:
            lines.append("")
            lines.append("*Spaces progression:*")
            lines.extend(format_spaces_progress_lines(rows, max_items=5))
        else:
            lines.append("")
            lines.append("_No spaces discovered in project/global scope yet._")

        tier = (getattr(self, "_user_model_prefs", {}) or {}).get(user_id, "")
        lines.append("")
        lines.append(f"Model tier: `{tier or 'auto'}`")

        await self.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")

    async def _handle_user(self, chat_id: int, user_id: int, username: str) -> None:
        """Show user profile, preferences, and session state (/user)."""
        lines: list[str] = [f"👤 *User Profile* — @{username or 'unknown'}", ""]
        lines.append(f"🆔 User ID: `{user_id}`")
        lines.append(f"💬 Chat ID: `{chat_id}`")

        # Auth status
        if getattr(self, "allowed_users", None):
            is_allowed = user_id in self.allowed_users
            lines.append(
                f"🔐 Auth: {'✅ Allowed' if is_allowed else '❌ Not in allowed list'}"
            )

        # Model tier preference
        tier = (getattr(self, "_user_model_prefs", {}) or {}).get(user_id, "")
        lines.append(f"🧠 Model tier: `{tier or 'auto'}`")

        # Voice & focus from session
        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                session = sm.get_or_create_session(chat_id, user_id)
                voice_on = getattr(session, "voice_replies_enabled", False)
                focus = getattr(session, "focus_mode", "balance")
                lines.append(f"🔊 Voice replies: {'on' if voice_on else 'off'}")
                lines.append(f"🎯 Focus mode: `{focus}`")
                # Session count
                try:
                    all_s = sm.get_all_sessions_for_user(user_id)
                    lines.append(f"📝 Sessions: {len(all_s)}")
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
            except Exception as _e:
                logger.debug("_handle_user session fetch failed: %s", _e)

        # Debug mode flag
        if getattr(self, "_debug_users", None) and user_id in self._debug_users:
            lines.append("🔍 Debug mode: on")

        lines.append("")
        lines.append(
            "`/settings` to configure · `/voice` for audio · `/status` for routing"
        )
        await self.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")

    async def _handle_mode(self, chat_id: int, mode_arg: str, user_id: int = 0) -> None:
        """Set focus/behavior mode. Uses MOOD_REGISTRY with fuzzy matching."""
        from navig.agent.soul import MOOD_REGISTRY, get_mood_profile

        _uid = user_id or chat_id

        if not mode_arg or mode_arg in ("help", "list"):
            lines = ["<b>Focus Modes</b>\n"]
            current = "balance"
            if _HAS_SESSIONS:
                try:
                    sm = get_session_manager()
                    session = sm.get_or_create_session(chat_id, _uid)
                    current = session.focus_mode
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
            for mid, mp in MOOD_REGISTRY.items():
                active_marker = " <b>- active</b>" if mid == current else ""
                lines.append(
                    f"{mp.emoji} <code>{mid}</code>{active_marker}\n<i>{mp.character}</i>"
                )
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
                    sm.update_settings(chat_id, _uid, focus_mode="balance")
                except Exception as e:
                    logger.debug("Failed to clear focus mode: %s", e)
            try:
                from navig.agent.proactive.user_state import get_user_state_tracker

                get_user_state_tracker().set_preference("chat_mode", "auto")
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
            await self.send_message(
                chat_id, "- auto. reading the room.", parse_mode=None
            )
            return

        try:
            mood = get_mood_profile(mode_arg)
        except KeyError:
            available = "  ".join(f"<code>{k}</code>" for k in MOOD_REGISTRY)
            await self.send_message(
                chat_id,
                f"Unknown mode: <code>{mode_arg}</code>\n\nAvailable: {available}",
                parse_mode="HTML",
            )
            return

        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                sm.update_settings(chat_id, _uid, focus_mode=mood.id)
            except Exception as e:
                logger.debug("Failed to persist focus_mode: %s", e)
        try:
            from navig.agent.proactive.user_state import get_user_state_tracker

            get_user_state_tracker().set_preference("chat_mode", mood.id)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        await self.send_message(chat_id, mood.transition_message, parse_mode=None)

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

    async def _handle_models_command(self, chat_id: int, user_id: int = 0) -> None:
        """Show active model config with interactive switcher keyboard (/models)."""
        try:
            from navig.agent.ai_client import get_ai_client

            client = get_ai_client()
            router = client.model_router

            lines = ["- *Model Routing*\n"]

            bridge_ok, bridge_disp = await self._probe_bridge_grid()
            lines.append(_format_bridge_status(bridge_ok, bridge_disp))

            best = (
                client._detect_best_provider()
                if hasattr(client, "_detect_best_provider")
                else "unknown"
            )
            lines.append(f"- best: `{best}`")

            try:
                from navig.llm_router import get_llm_router

                llm_router = get_llm_router()
                if llm_router:
                    lines.append("\n- *LLM Mode Router* - primary:")
                    mode_icons = {
                        "small_talk": "-",
                        "big_tasks": "-",
                        "coding": "-",
                        "summarize": "-",
                        "research": "-",
                    }
                    for mode_name in (
                        "small_talk",
                        "big_tasks",
                        "coding",
                        "summarize",
                        "research",
                    ):
                        mc = llm_router.modes.get_mode(mode_name)
                        if mc:
                            icon = mode_icons.get(mode_name, "-")
                            display_name = mode_name.replace("_", " ")
                            lines.append(
                                f"  {icon} {display_name}: `{mc.provider}:{mc.model}`"
                            )
                            if mc.fallback_provider:
                                lines.append(
                                    f"  - `{mc.fallback_provider}:{mc.fallback_model}`"
                                )
                else:
                    lines.append("\n_LLM Mode Router: not active_")
            except Exception:
                lines.append("\n_LLM Mode Router: unavailable_")

            if router and router.is_active:
                cfg = router.cfg
                lines.append(f"\n- *Hybrid Router* - fallback, mode=`{cfg.mode}`:")
                user_pref = self._user_model_prefs.get(user_id, "")
                pref_label = {
                    "small": "- Small",
                    "big": "- Big",
                    "coder_big": "- Coder",
                }.get(user_pref, "- Auto")
                lines.append(f"  - preset: *{pref_label}*")
                for label, slot in [
                    ("- small", cfg.small),
                    ("- big", cfg.big),
                    ("- coder", cfg.coder_big),
                ]:
                    lines.append(
                        f"  {label}: `{slot.provider or '-'}:{slot.model or '-'}`"
                    )
            else:
                lines.append("\n_Hybrid Router: disabled_")

            try:
                from navig.agent.llm_providers import GitHubModelsProvider

                lines.append("\n- *GitHub Models chains:*")
                for chain_name, models in GitHubModelsProvider.FALLBACK_CHAINS.items():
                    model_list = " - ".join(m.split(":")[-1] for m in models)
                    lines.append(f"  - {chain_name.replace('_', ' ')}: {model_list}")
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

            user_pref = self._user_model_prefs.get(user_id, "")
            check = lambda t: " -" if user_pref == t else ""  # noqa: E731
            keyboard = [
                [
                    {
                        "text": f"- Small{check('small')}",
                        "callback_data": "ms_tier_small",
                    },
                    {"text": f"- Big{check('big')}", "callback_data": "ms_tier_big"},
                    {
                        "text": f"- Code{check('coder_big')}",
                        "callback_data": "ms_tier_coder",
                    },
                ],
                [
                    {"text": f"- Auto{check('')}", "callback_data": "ms_tier_auto"},
                    {"text": "- Full table", "callback_data": "ms_info"},
                ],
                [{"text": "- Providers ->", "callback_data": "ms_providers"}],
            ]
            await self.send_message(chat_id, "\n".join(lines), keyboard=keyboard)

        except Exception as e:
            await self.send_message(chat_id, f"- Could not read routing info: {e}")

    async def _handle_providers(self, chat_id: int) -> None:
        """AI Provider Hub - Bridge Grid probe, live registry status (/providers)."""
        bridge_online, bridge_url = await self._probe_bridge_grid()

        active_prov = ""
        if bridge_online:
            active_prov = "bridge_copilot"
        else:
            try:
                from navig.llm_router import get_llm_router

                lr = get_llm_router()
                if lr:
                    m = lr.modes.get("big_tasks")
                    if m and getattr(m, "provider", None):
                        active_prov = m.provider
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        try:
            from navig.providers.registry import list_enabled_providers
            from navig.providers.verifier import verify_provider

            providers = list_enabled_providers()
        except Exception:
            providers = []

        lines = ["- *Bridge Grid - AI Provider Hub*\n"]
        lines.append(_format_bridge_status(bridge_online, bridge_url))
        if bridge_online:
            lines.append("- _Non-Bridge providers are fallback only._")
        else:
            lines.append("- _Connect VS Code + navig\\-bridge to activate._")

        keyboard_rows: list = [
            [
                {
                    "text": f"- Bridge Grid - {'online -' if bridge_online else 'offline'}",
                    "callback_data": "prov_bridge",
                }
            ],
        ]
        button_row: list = []

        for manifest in providers:
            if manifest.id == "mcp_bridge":
                continue
            try:
                result = verify_provider(manifest)
                if manifest.tier == "local" and manifest.local_probe:
                    ready = result.local_probe_ok
                elif manifest.requires_key:
                    ready = result.key_detected
                else:
                    ready = True
            except Exception:
                ready = False

            if not ready:
                continue

            is_active = manifest.id == active_prov
            prefix = "✅ " if is_active else ""
            btn = {
                "text": f"{prefix}{manifest.emoji} {manifest.display_name}",
                "callback_data": f"prov_{manifest.id}",
            }
            button_row.append(btn)
            if len(button_row) == 2:
                keyboard_rows.append(list(button_row))
                button_row = []

        if button_row:
            keyboard_rows.append(list(button_row))

        keyboard_rows.append(
            [{"text": "🚫 No AI  — raw mode", "callback_data": "prov_noai"}]
        )
        keyboard_rows.append([{"text": "✖ Close", "callback_data": "prov_close"}])

        await self.send_message(chat_id, "\n".join(lines), keyboard=keyboard_rows)

    @error_handled
    @typing_context
    async def _show_provider_model_picker(self, chat_id: int, prov_id: str) -> None:
        """Send a model-tier assignment picker for the given provider."""
        emoji, name, models = "-", prov_id, []
        try:
            from navig.providers.registry import _INDEX as PROV_INDEX

            manifest = PROV_INDEX.get(prov_id)
            if manifest:
                emoji = manifest.emoji
                name = manifest.display_name
                models = list(manifest.models)
        except Exception:
            manifest = None

        if prov_id == "ollama":
            try:
                import aiohttp

                async with (
                    aiohttp.ClientSession() as session,
                    session.get("http://127.0.0.1:11434/api/tags", timeout=2) as r,
                ):
                    data = await r.json()
                    live = [m["name"] for m in data.get("models", []) if m.get("name")]
                    if live:
                        models = live
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
            if not models:
                models = ["qwen2.5:7b", "qwen2.5:3b", "phi3.5", "llama3.2"]

        models = models[:8]

        if not models:
            await self.send_message(chat_id, f"- No models found for `{prov_id}`.")
            return

        current: dict = {"small": "-", "big": "-", "coder_big": "-"}
        try:
            from navig.agent.ai_client import get_ai_client

            router = get_ai_client().model_router
            if router and router.is_active:
                for tier in ("small", "big", "coder_big"):
                    slot = router.cfg.slot_for_tier(tier)
                    if slot.provider == prov_id:
                        current[tier] = f"`{slot.model}`"
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        lines = [
            f"{emoji} *{name}* - assign model to tier",
            "",
            f"  - Small:  {current['small']}",
            f"  - Big:    {current['big']}",
            f"  - Code:   {current['coder_big']}",
            "",
            "Tap -S / -B / -C next to a model to assign it to that tier:",
        ]
        for i, m in enumerate(models):
            lines.append(f"  `{i}.` {m}")

        keyboard = []
        for i, m in enumerate(models):
            short = m.split("/")[-1].split(":")[-1][:10]
            keyboard.append(
                [
                    {"text": f"-S {short}", "callback_data": f"pm_{prov_id}_{i}_s"},
                    {"text": f"-B {short}", "callback_data": f"pm_{prov_id}_{i}_b"},
                    {"text": f"-C {short}", "callback_data": f"pm_{prov_id}_{i}_c"},
                ]
            )
        keyboard.append([{"text": "- Providers", "callback_data": "prov_back"}])
        await self.send_message(chat_id, "\n".join(lines), keyboard=keyboard)

    async def _handle_providers_and_models(
        self, chat_id: int, user_id: int = 0, is_group: bool = False
    ) -> None:
        """Combined view: model routing table + AI provider hub."""
        await self._handle_models_command(chat_id, user_id)
        await self._handle_providers(chat_id)

    # -- Diagnostics -----------------------------------------------------------

    async def _handle_debug(self, chat_id: int) -> None:
        """Show daemon debug info (/debug)."""
        import sys

        lines = ["- *Debug*\n"]
        lines.append(f"Python: `{sys.version.split()[0]}`")
        try:
            import navig as _navig_pkg

            lines.append(f"navig pkg: `{getattr(_navig_pkg, '__file__', 'unknown')}`")
            lines.append(f"version: `{getattr(_navig_pkg, '__version__', 'unknown')}`")
        except Exception as e:
            lines.append(f"navig: - `{e}`")
        try:
            from navig.platform import paths as _paths
            from navig.vault import get_vault_v2

            v = get_vault_v2()
            count = len(v.list()) if hasattr(v, "list") else "?"
            lines.append(f"vault: - `{count} entries` ({_paths.vault_dir()})")
        except Exception as e:
            try:
                from navig.platform import paths as _paths

                vpath = str(_paths.vault_dir())
            except Exception:
                vpath = "?"
            lines.append(f"vault: - `{e}` - path: `{vpath}`")
        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                s_list = sm.list_sessions() if hasattr(sm, "list_sessions") else []
                lines.append(f"sessions: `{len(s_list)} loaded`")
            except Exception:
                lines.append("sessions: -")
        try:
            from navig.gateway.channels.telegram_voice import _HAS_VOICE as _hv

            lines.append(f"HAS\\_VOICE: `{_hv}`")
        except Exception:
            lines.append("HAS\\_VOICE: `unknown`")
        lines.append(f"HAS\\_KEYBOARDS: `{_HAS_KEYBOARDS}`")
        lines.append(f"HAS\\_SESSIONS: `{_HAS_SESSIONS}`")
        pp = os.environ.get("PYTHONPATH", "_(not set)_")
        lines.append(f"PYTHONPATH: `{pp}`")
        try:
            from navig.vault.resolver import resolve_secret

            dg = resolve_secret(
                ["DEEPGRAM_KEY", "DEEPGRAM_API_KEY"],
                ["deepgram/api_key", "deepgram/api-key", "deepgram_api_key"],
            )
        except Exception:
            dg = os.environ.get("DEEPGRAM_KEY") or os.environ.get("DEEPGRAM_API_KEY")
        lines.append(f"DEEPGRAM\\_KEY: `{'- set' if dg else '- missing'}`")
        await self.send_message(chat_id, "\n".join(lines))

    @rate_limited
    @error_handled
    async def _handle_trace(self, chat_id: int, user_id: int) -> None:
        """Show recent activity snapshot (/trace).

        Covers: LLM bridges - recent messages - session state - daemon warnings - vault.
        """
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        SEP = "-"
        now_utc = _dt.now(_tz.utc).strftime("%H:%M UTC")
        lines: list = [f"- *Recent Trace* - {now_utc}", SEP]

        _bridge_online, _bridge_url = await self._probe_bridge_grid()
        lines.append("- *Routing*")
        lines.append(
            f"  - Bridge Grid `{_bridge_url}` - *online*"
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
                            f"  {icon} {label} -> `{getattr(mc, 'provider', '?')}:{getattr(mc, 'model', '?')}`"
                        )
            except Exception:
                lines.append("  _(model router unavailable)_")

        lines.append(SEP)

        # -- Session messages - triple-fallback (DUP-10 fix: extracted helper) -
        session_messages = self._load_recent_messages(user_id)
        all_sessions_count = 0
        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                all_sessions_count = len(sm.sessions)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        lines.append(
            f"- *Memory* - {len(session_messages)} msgs - {all_sessions_count} session(s)"
        )
        lines.append(SEP)

        lines.append("- *Recent*")
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
                                _dt.utcfromtimestamp(ts_raw).strftime("%H:%M") + " "
                            )
                        else:
                            ts_prefix = str(ts_raw)[:5] + " "
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical
                lines.append(f"  {ts_prefix}{arrow} {actor}: {preview}")
        else:
            lines.append("  _(no recent activity)_")

        lines.append(SEP)

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
                _sk = f"agent:default:telegram:default:dm:{user_id}"
                _s = sm.sessions.get(_sk)
                if _s is not None:
                    voice_label = (
                        "on" if _s.metadata.get("voice_enabled", False) else "off"
                    )
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        lines.append(
            f"-  *Session* - tier: `{tier_label}` - host: `{active_host}` - voice: `{voice_label}`"
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
                daemon_issues = [
                    ln.strip() for ln in _tail if any(kw in ln.lower() for kw in _kw)
                ]
                break
            except OSError:
                pass  # best-effort cleanup

        if daemon_issues:
            lines.append("- *Daemon Warnings*")
            for issue in daemon_issues[-5:]:
                display = issue if len(issue) <= 100 else issue[:97] + "-"
                lines.append(f"  -  `{display}`")
        else:
            lines.append("- *Daemon* - - no warnings")

        try:
            from navig.vault import get_vault_v2

            _v = get_vault_v2()
            _items = _v.list() if hasattr(_v, "list") else []
            vault_msg = f"- {len(_items)} entries"
        except Exception as _ve:
            vault_msg = f"- {str(_ve)[:60]}"
        lines.append(f"- *Vault* - {vault_msg}")
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

    def _load_recent_messages(self, user_id: int) -> list:
        """Load recent session messages via three fallback paths.

        1. SessionManager in-memory cache
        2. navig.agent.memory module
        3. msg_trace.jsonl file

        Returns a list of ``{"role": ..., "content": ...}`` dicts.
        """
        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                sk = f"agent:default:telegram:default:dm:{user_id}"
                raw_session = sm.sessions.get(sk)
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
                                "content": entry.get("content")
                                or entry.get("text")
                                or "",
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
            await self.send_message(
                chat_id, "- Restarting navig-daemon in 3s-", parse_mode=None
            )
            sudo_pass = os.environ.get("SUDO_PASS", "")
            if sudo_pass:
                # Pass password via env var - never interpolated into shell string (SEC-3 fix)
                cmd = [
                    "bash",
                    "-c",
                    "sleep 3 && printf '%s\n' \"$_NAVIG_SUDOPW\" | sudo -S systemctl restart navig-daemon",
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
                    "sleep 3 && sudo systemctl restart navig-daemon",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
        else:
            await self._handle_cli_command(
                chat_id, user_id, metadata, f"docker restart {arg}"
            )

    # -- Audio / settings menus ------------------------------------------------

    async def _handle_audio_menu(
        self, chat_id: int, user_id: int, is_group: bool = False
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
        await self.send_message(
            chat_id, _audio_header_text(session), keyboard=keyboard_rows
        )

    async def _handle_voice_menu(
        self, chat_id: int, user_id: int, is_group: bool = False
    ) -> None:
        """Send the /voice provider picker (provider -> model -> voice/speed/format)."""
        if _HAS_AUDIO_MENU:
            try:
                cfg = _load_audio_config(user_id)
                await self.send_message(
                    chat_id,
                    _audio_screen_a_text(cfg),
                    keyboard=_audio_screen_a_kb(cfg),
                )
                return
            except Exception as _am_err:
                logger.debug("Deep audio menu failed, falling back: %s", _am_err)

        await self._handle_audio_menu(chat_id, user_id, is_group)

    # Backward-compat alias
    _handle_settings_menu = _handle_audio_menu

    async def _handle_settings_hub(
        self, chat_id: int, user_id: int, is_group: bool = False
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
        await self.send_message(
            chat_id, _settings_hub_text(session), keyboard=keyboard_rows
        )

    # -- Formatting & Reasoning -------------------------------------------------

    async def _handle_format(
        self,
        chat_id: int,
        user_id: int,
        text_arg: str = "",
    ) -> None:
        """/format [text] — convert Markdown to Telegram Unicode format."""
        from navig.gateway.channels.telegram_formatter import (
            MarkdownFormatter,
            get_formatter_store,
        )

        store = get_formatter_store()
        prefs = store.get(user_id)
        formatter = MarkdownFormatter()

        if not text_arg:
            # Show settings panel if no text provided
            from navig.gateway.channels.telegram_formatter import (
                build_formatter_settings_keyboard,
            )

            keyboard = build_formatter_settings_keyboard(prefs)
            await self.send_message(
                chat_id,
                "*Markdown Formatter Settings*\n\nSend `/format <text>` to convert, or adjust preferences below.",
                parse_mode="Markdown",
                keyboard=keyboard,
            )
            return

        chunks = formatter.convert_chunked(text_arg, prefs)
        for chunk in chunks:
            await self.send_message(chat_id, chunk, parse_mode=None)

    async def _handle_think(
        self,
        chat_id: int,
        user_id: int,
        topic: str = "",
        metadata: Any = None,
    ) -> None:
        """/think <topic> — reason via LLM, output in paginated cards."""
        if not topic:
            await self.send_message(
                chat_id,
                "Usage: `/think <your topic or question>`\n\nI'll reason through it step by step, delivered as swipeable cards.",
                parse_mode="Markdown",
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
        await nav.create(
            chat_id=chat_id, user_id=user_id, topic=topic, llm_text=llm_text
        )

    async def _handle_refine_cmd(
        self,
        chat_id: int,
        user_id: int,
        topic: str = "",
        metadata: Any = None,
    ) -> None:
        """/refine [text] — start the AI clarification + refinement loop."""
        if not topic:
            await self.send_message(
                chat_id,
                "Usage: `/refine <your text or idea>`\n\nI'll ask 3 clarifying questions, then produce a refined version.",
                parse_mode="Markdown",
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

    # -- Briefing / deck -------------------------------------------------------

    @rate_limited
    @error_handled
    @typing_context
    async def _handle_briefing(
        self, chat_id: int, user_id: int, metadata: MessageMetadata
    ) -> None:
        """Real-data system briefing - no AI, no invented content (/briefing)."""
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        now = _dt.now(_tz.utc)
        lines: list = [
            f"- *System Briefing* - {now.strftime('%H:%M UTC, %d %b')}",
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
            icon = "-" if state == "active" else "-"
            lines.append(f"{icon} *Daemon:* {state}{since}")
        except Exception:
            lines.append("- *Daemon:* status unavailable")

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
        lines.append(
            f"- *LLM Bridge:* {'online (bridge_copilot)' if bridge_ok else 'offline'}"
        )

        try:
            from navig.vault import get_vault_v2

            v = get_vault_v2()
            key_count = len(v.list()) if hasattr(v, "list") else "?"
            lines.append(f"- *Vault:* {key_count} keys stored")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                lines.append(f"- *Sessions:* {len(sm.sessions)} active")
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
                timeout=2.0,
            )
            stdout, _ = await p.communicate()
            lines.append(f"- *Server:* {stdout.decode().strip()}")
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
                timeout=2.0,
            )
            stdout, _ = await p.communicate()
            dfl = stdout.decode().strip().splitlines()
            if len(dfl) >= 2:
                parts = dfl[1].split()
                if len(parts) >= 3:
                    lines.append(
                        f"- *Disk:* {parts[0]} used, {parts[1]} free ({parts[2]})"
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
                                recent.append(f"  - `{content}`")
                        except Exception:  # noqa: BLE001
                            pass  # best-effort; failure is non-critical
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        if recent:
            lines.append("*Recent commands:*")
            lines.extend(recent[-5:])
        else:
            lines.append("_No recent command history._")

        try:
            from navig.spaces.progress import collect_spaces_progress

            spaces = collect_spaces_progress()
            if spaces:
                lines.append("-" * 22)
                lines.append("*Spaces Progress:*")
                for row in spaces[:5]:
                    lines.append(
                        f"  - `{row.name}` ({row.scope}) — {row.completion_pct:.1f}%"
                    )
        except Exception:
            pass

        await self.send_message(chat_id, "\n".join(lines))

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
                    f"  `{s.id}` - {s.name}"
                    for s in sorted(index.values(), key=lambda x: x.id)[:20]
                )
                await self.send_message(
                    chat_id,
                    f"- Skill `{skill_id}` not found.\n\nAvailable:\n{available}",
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
                    output_text = (
                        result.output.get("output") or result.output.get("info") or ""
                    )
                else:
                    output_text = str(result.output or "")

                header = f"- **{skill_name}**" + (f" - `{command}`" if command else "")
                msg = (
                    f"{header}\n\n{output_text[:3800]}"
                    if output_text
                    else f"{header}\n- Done."
                )
                await self.send_message(chat_id, msg)
            else:
                await self.send_message(
                    chat_id, f"- Skill error:\n{result.error}", parse_mode=None
                )

        except Exception as exc:
            await self.send_message(
                chat_id, f"- /skill crashed: {exc}", parse_mode=None
            )

    async def _skill_list(self, chat_id: int) -> None:
        """Send a paginated list of all available skills."""
        try:
            from navig.skills.loader import load_all_skills

            skills = load_all_skills()
        except Exception as exc:
            await self.send_message(
                chat_id, f"- Could not load skills: {exc}", parse_mode=None
            )
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

        lines: list[str] = ["- **Available Skills**\n"]
        for cat, cat_skills in sorted(by_cat.items()):
            lines.append(f"\n**{cat.title()}**")
            for s in cat_skills:
                safety_icon = {"safe": "-", "elevated": "-", "destructive": "-"}.get(
                    s.safety, "-"
                )
                lines.append(f"  {safety_icon} `{s.id}` - {s.name}")

        lines.append(
            "\n\nUsage: `/skill <id>` for info - `/skill <id> <command>` to run"
        )
        await self.send_message(chat_id, "\n".join(lines))

    # -- CLI command dispatch --------------------------------------------------

    def _match_cli_command(self, text: str) -> str | None:
        """Match a slash command to a navig CLI string.  Returns None if no match."""
        import shlex

        parts = text.strip().split(None, 1)
        if not parts:
            return None
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        for entry in _SLASH_REGISTRY:
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
                        await self.send_message(
                            chat_id, response[:3950], parse_mode=None
                        )
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
                    if nl_response and not nl_response.startswith(
                        "Command exited with code"
                    ):
                        response = nl_response
                except Exception as _nl_err:
                    import logging as _log

                    _log.getLogger(__name__).warning(
                        "NLP formatting failed for cli command: %s", _nl_err
                    )

                await self.send_message(chat_id, response, parse_mode=None)
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
        commands = [
            {"command": e.command, "description": e.description}
            for e in _SLASH_REGISTRY
            if e.visible
        ]
        # Deck command is opt-in: only added to the bot's command list when
        # telegram.deck_url is configured.  Users without a Deck deployment
        # never see the /deck command in the "/" popup.
        deck_url = self._get_deck_url()
        if deck_url:
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
                    logger.debug(
                        "Could not read deck_url from config %s: %s", cfg_path, e
                    )
        return None

    # ── Digital Ghost / Laravel Port Features ───────────────────────────────

    async def _handle_about(self, chat_id: int) -> None:
        """Learn about the bot origin."""
        msg = (
            "🤖 *Digital Ghost*\n\n"
            "An advanced cybernetic entity running within the SCHEMA network. "
            "Originally designed for the Laravel ecosystem, now evolved into a pure Python "
            "NAVIG context.\n\n"
            "Use `/help` to see my capabilities."
        )
        await self.send_message(chat_id, msg)

    async def _handle_auto_start(self, chat_id: int, user_id: int, text: str) -> None:
        """Start AI conversational auto-reply using durable runtime state."""
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
            await self.send_message(
                chat_id, f"✅ Auto-replies *ACTIVATED* with persona: `{role}`"
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
            from navig.store.runtime import get_runtime_store

            state = get_runtime_store().get_ai_state(user_id)
        except Exception as e:
            logger.error("Failed to read auto-reply state: %s", e)

        if state and state.get("mode") == "active":
            role = state.get("persona") or "assistant"
            await self.send_message(
                chat_id, f"✅ AI is currently *ACTIVE* in `{role}` mode."
            )
            return

        await self.send_message(chat_id, "🛑 AI auto-reply is currently *INACTIVE*.")

    async def _handle_auto_roles(self, chat_id: int) -> None:
        roles = (
            "• `storyteller`\n• `assistant`\n• `philosopher`\n• `teacher`\n• `tyler`"
        )
        await self.send_message(
            chat_id,
            f"🎭 *Available AI Personas:*\n\n{roles}\n\nUse `/auto_start <role>` to activate.",
        )

    async def _handle_explain_ai(self, chat_id: int, text: str) -> None:
        topic = text[len("/explain_ai") :].strip()
        if not topic:
            await self.send_message(
                chat_id,
                "Please provide a topic. Example: `/explain_ai quantum computing`",
            )
            return

        await self.send_typing(chat_id)
        try:
            from navig.ai.llm_router import route_llm

            prompt = f"Explain the following topic clearly and comprehensively, structured for a Telegram message:\n\nTopic: {topic}"
            response = await route_llm(
                prompt, tier="small", system_prompt="You are an expert explainer."
            )
            await self.send_message(chat_id, response.text)
        except Exception as e:
            await self.send_message(
                chat_id, f"❌ Failed to explain: {e}", parse_mode=None
            )

    async def _handle_music(self, chat_id: int) -> None:
        await self.send_message(
            chat_id,
            "🎵 *Music Conversion*\n\nIntegration with Spotify/Apple Music APIs pending migration.",
            parse_mode="Markdown",
        )

    async def _handle_imagegen(self, chat_id: int, text: str) -> None:
        prompt = text[len("/imagegen") :].strip()
        if not prompt:
            await self.send_message(
                chat_id,
                "Please provide a prompt. Example: `/imagegen cybernetic city sunset`",
                parse_mode="Markdown",
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
        await self.send_message(
            chat_id, "✊ Respect system ledger is syncing...", parse_mode=None
        )

    async def _handle_currency(self, chat_id: int, text: str) -> None:
        await self.send_message(
            chat_id, "💹 Real-time fiat exchange rates are offline.", parse_mode=None
        )

    async def _handle_crypto_list(self, chat_id: int) -> None:
        await self.send_message(
            chat_id,
            "🪙 *Crypto Reference:*\n• BTC - Bitcoin\n• ETH - Ethereum\n• SOL - Solana\n\n(Price feed offline)",
            parse_mode="Markdown",
        )

    def _parse_remindme_request(self, text: str) -> tuple[datetime | None, str, str | None]:
        """Parse /remindme payload to (remind_at_utc, message, error)."""
        arg = text[len("/remindme") :].strip()
        if not arg:
            return (
                None,
                "",
                "Usage: `/remindme in 30 minutes check logs` or `/remindme at 21:30 deploy review`",
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

        abs_match = re.match(
            r"^at\s+(\d{1,2}):(\d{2})\s+(.+)$", arg, flags=re.IGNORECASE
        )
        if abs_match:
            hour = int(abs_match.group(1))
            minute = int(abs_match.group(2))
            msg = abs_match.group(3).strip()
            if hour > 23 or minute > 59:
                return None, "", "Time must be in 24h format, e.g. `at 21:30`."
            if not msg:
                return None, "", "Reminder message cannot be empty."
            now = datetime.now(timezone.utc)
            remind_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if remind_at <= now:
                remind_at = remind_at + timedelta(days=1)
            return remind_at, msg, None

        return (
            None,
            "",
            "Could not parse reminder. Use `/remindme in 10 minutes <message>` or `/remindme at 18:45 <message>`.",
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
        when = remind_at.strftime("%Y-%m-%d %H:%M UTC")
        await self.send_message(
            chat_id,
            f"⏰ Reminder set.\nID: `{reminder_id}`\nWhen: `{when}`\nMessage: {message}",
        )

    async def _handle_myreminders(self, chat_id: int, user_id: int) -> None:
        from navig.store.runtime import get_runtime_store

        reminders = get_runtime_store().get_user_reminders(user_id)
        if not reminders:
            await self.send_message(chat_id, "📭 You have no active reminders.")
            return

        lines = ["⏰ *Your Active Reminders*", ""]
        for row in reminders:
            rid = row.get("id")
            remind_at = str(row.get("remind_at") or "").replace("T", " ")[:16]
            msg = str(row.get("message") or "").strip()
            lines.append(f"`#{rid}` at `{remind_at} UTC` — {msg}")
        lines.append("\nUse `/cancelreminder <id>` to remove one.")
        await self.send_message(chat_id, "\n".join(lines))

    async def _handle_cancelreminder(self, chat_id: int, user_id: int, text: str) -> None:
        arg = text[len("/cancelreminder") :].strip()
        if not arg.isdigit():
            await self.send_message(chat_id, "Usage: `/cancelreminder <id>`")
            return

        reminder_id = int(arg)
        from navig.store.runtime import get_runtime_store

        deleted = get_runtime_store().cancel_reminder(reminder_id, user_id)
        if deleted:
            await self.send_message(chat_id, f"✅ Reminder `{reminder_id}` cancelled.")
        else:
            await self.send_message(
                chat_id,
                f"❌ No active reminder found for id `{reminder_id}`.",
                parse_mode=None,
            )

    async def _handle_stats_global(self, chat_id: int) -> None:
        await self.send_message(
            chat_id, "📊 Global chat statistics are gathering data..."
        )

    async def _handle_choice(self, chat_id: int, text: str) -> None:
        args = text[len("/choice") :].strip()
        if " or " not in args.lower():
            await self.send_message(
                chat_id,
                "Please use 'or' to separate choices. Example: `/choice pizza or burger`",
                parse_mode="Markdown",
            )
            return
        import random

        choices = [c.strip() for c in args.lower().split(" or ") if c.strip()]
        if choices:
            await self.send_message(chat_id, f"🎲 I choose: *{random.choice(choices)}*")
        else:
            await self.send_message(chat_id, "Invalid choices.", parse_mode=None)

    async def _handle_kick(self, chat_id: int, text: str) -> None:
        target = text[len("/kick") :].strip()
        await self.send_message(
            chat_id,
            f"👢 Core restriction: Bot requires channel Admin rights to ban `{target}`.",
            parse_mode="Markdown",
        )

    async def _handle_mute(self, chat_id: int, text: str) -> None:
        target = text[len("/mute") :].strip()
        await self.send_message(
            chat_id,
            f"🔇 Core restriction: Bot requires channel Admin rights to restrict `{target}`.",
            parse_mode="Markdown",
        )

    async def _handle_unmute(self, chat_id: int, text: str) -> None:
        target = text[len("/unmute") :].strip()
        await self.send_message(
            chat_id,
            f"🔊 Core restriction: Bot requires channel Admin rights to pardon `{target}`.",
            parse_mode="Markdown",
        )

    async def _handle_search(self, chat_id: int, text: str) -> None:
        query = text[len("/search") :].strip()
        await self.send_message(
            chat_id, f"🔍 User search proxy offline for query: `{query}`"
        )
