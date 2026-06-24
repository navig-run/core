"""
Natural-language settings-intent module.

Recognises phrases that previously required slash commands (/mode, /big, /small,
/coder, /auto, /ai) and applies the corresponding config change directly. This
lets the user manage settings conversationally — "switch focus to deep work",
"use big model" — so the slash commands can be deleted from the Telegram
surface entirely while the underlying functionality stays reachable.

The matcher is a fast keyword regex (no LLM call) so it works in no-AI focus
modes too. Ambiguity / fuzzy matching is deferred to an LLM classifier we can
layer on later.

Public entry point::

    handled = await try_handle_settings_intent(text, chat_id, user_id, channel)
    if handled:
        return  # caller should stop further dispatch

Returns True only when an intent matched AND was applied (or rejected with a
visible error message). Returns False for any text that doesn't look like a
settings request, so the caller falls through to the normal LLM flow.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.gateway.channels.telegram import TelegramChannel

logger = logging.getLogger(__name__)

# Canonical focus modes. Map common aliases to a canonical id.
# Keep this list in sync with the Deck Account → Focus Mode picker.
_FOCUS_MODE_ALIASES: dict[str, str] = {
    # canonical → itself
    "navig": "navig",
    "work": "work",
    "deep-focus": "deep-focus",
    "deepfocus": "deep-focus",
    "deep": "deep-focus",
    "planning": "planning",
    "creative": "creative",
    "relax": "relax",
    "sleep": "sleep",
    "balance": "balance",
    "balanced": "balance",
    "coder": "coder",
    "auto": "auto",
    # common phrasings
    "deep work": "deep-focus",
    "deep-work": "deep-focus",
    "deep focus": "deep-focus",
    "focus mode": "deep-focus",
    "planning mode": "planning",
    "creative mode": "creative",
    "relax mode": "relax",
    "sleep mode": "sleep",
    "coding": "coder",
    "code": "coder",
    "automatic": "auto",
}

# Tier override aliases (the canonical values mirror _handle_tier_command).
_TIER_ALIASES: dict[str, str] = {
    "big": "big",
    "large": "big",
    "small": "small",
    "tiny": "small",
    "fast": "small",
    "coder": "coder_big",
    "code": "coder_big",
    "coding": "coder_big",
    "coder-big": "coder_big",
    "auto": "",
    "automatic": "",
    "default": "",
    "reset": "",
}

# Focus mode regex — matches "set focus to X", "switch mode to X", "focus = X",
# "go deep focus", "I want X mode", "use X mode", and similar variations.
_FOCUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:set|switch|change|put|go)\s+(?:my\s+)?(?:focus|mode|focus\s+mode)\s*"
        r"(?:to|on|=|into)?\s+([a-z\- ]{3,20})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:focus|mode)\s*[:=]\s*([a-z\- ]{3,20})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:use|enable|activate)\s+([a-z\- ]{3,20})\s+(?:focus|mode)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:i\s+want|i'?d\s+like|i\s+need)\s+([a-z\- ]{3,20})\s+(?:focus|mode)\b",
        re.IGNORECASE,
    ),
]

# Tier override regex — "use big model", "switch to small", "set tier to coder",
# "auto model", "default tier".
_TIER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:use|switch\s+to|set|change\s+to|pick)\s+(?:the\s+)?([a-z\- ]{3,15})\s+(?:model|tier)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:tier|model)\s*[:=]\s*([a-z\- ]{3,15})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b([a-z\- ]{3,15})\s+(?:model|tier)\s+please\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:auto|automatic|default|reset)\s+(?:model|tier)\b",
        re.IGNORECASE,
    ),
]


def _normalize_focus(raw: str) -> str | None:
    """Map a free-form focus phrase to a canonical mode id, or None."""
    key = raw.strip().lower().rstrip(".,!?")
    if not key:
        return None
    return _FOCUS_MODE_ALIASES.get(key)


def _normalize_tier(raw: str) -> str | None:
    """Map a free-form tier phrase to a canonical tier value, or None.

    Returns "" for explicit auto/reset (canonical empty string means auto in
    the existing _user_model_prefs map). Returns None when no match.
    """
    key = raw.strip().lower().rstrip(".,!?")
    if not key:
        return None
    return _TIER_ALIASES.get(key)


def _extract_focus_mode(text: str) -> str | None:
    """Try each focus-mode pattern; return the canonical mode or None."""
    for pat in _FOCUS_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        canonical = _normalize_focus(m.group(1))
        if canonical:
            return canonical
    return None


def _extract_tier(text: str) -> str | None:
    """Try each tier pattern; return the canonical tier ("", small, big, coder_big) or None."""
    for pat in _TIER_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        # Pattern 4 has no capture group (matches the leading word itself); fall through.
        try:
            cand = m.group(1)
        except IndexError:
            cand = m.group(0).split(" ", 1)[0]
        canonical = _normalize_tier(cand)
        if canonical is not None:
            return canonical
    return None


async def _apply_focus_mode(
    channel: "TelegramChannel",
    chat_id: int,
    user_id: int,
    mode: str,
) -> None:
    """Persist the focus mode change in both session metadata and user state."""
    is_group = chat_id != user_id
    # Session metadata
    try:
        from navig.gateway.channels.telegram_sessions import get_session_manager

        sm = get_session_manager()
        # Special-case "auto" → balance, mirroring _handle_mode's behavior.
        stored_value = "balance" if mode == "auto" else mode
        sm.set_session_metadata(
            chat_id, user_id, "focus_mode", stored_value, is_group=is_group
        )
    except Exception as exc:
        logger.debug("nl_settings: focus_mode session write failed: %s", exc)

    # User-state preference
    try:
        from navig.agent.proactive.user_state import get_user_state_tracker

        get_user_state_tracker().set_preference("chat_mode", mode)
    except Exception as exc:
        logger.debug("nl_settings: chat_mode preference write failed: %s", exc)


async def _apply_tier(
    channel: "TelegramChannel",
    chat_id: int,
    user_id: int,
    tier: str,
) -> None:
    """Persist the user's tier override (in-memory dict on the channel)."""
    if hasattr(channel, "_set_user_tier_pref"):
        try:
            channel._set_user_tier_pref(chat_id, user_id, tier)
            return
        except Exception as exc:
            logger.debug("nl_settings: _set_user_tier_pref failed: %s", exc)

    prefs = getattr(channel, "_user_model_prefs", None)
    if isinstance(prefs, dict):
        if tier:
            prefs[user_id] = tier
        else:
            prefs.pop(user_id, None)


_TIER_LABELS: dict[str, str] = {
    "": "Auto",
    "small": "Small",
    "big": "Big",
    "coder_big": "Coder",
}


# Voice-replies on/off — "turn voice replies on", "stop voice", "voice off".
_VOICE_REPLIES_ON_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:turn|switch|set)\s+(?:on\s+)?voice(?:\s+replies?)?\s*(?:on)?\b", re.IGNORECASE),
    re.compile(r"\benable\s+voice(?:\s+replies?)?\b", re.IGNORECASE),
    re.compile(r"\bvoice(?:\s+replies?)?\s*(?:=\s*)?on\b", re.IGNORECASE),
    re.compile(r"^/?voiceon\b", re.IGNORECASE),
]
_VOICE_REPLIES_OFF_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:turn|switch|set)\s+(?:off\s+)?voice(?:\s+replies?)?\s+off\b", re.IGNORECASE),
    re.compile(r"\bstop\s+voice(?:\s+replies?)?\b", re.IGNORECASE),
    re.compile(r"\bdisable\s+voice(?:\s+replies?)?\b", re.IGNORECASE),
    re.compile(r"\bvoice(?:\s+replies?)?\s*(?:=\s*)?off\b", re.IGNORECASE),
    re.compile(r"^/?voiceoff\b", re.IGNORECASE),
    re.compile(r"\btext\s+only\b", re.IGNORECASE),
]

# Voice name change — "switch voice to nova", "use voice onyx", "voice = alloy".
_VOICE_NAME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:switch|set|change|use|pick)\s+(?:my\s+)?voice\s*(?:to|=)?\s+([A-Za-z][A-Za-z0-9\-]{1,30})",
        re.IGNORECASE,
    ),
    re.compile(r"\bvoice\s*[:=]\s*([A-Za-z][A-Za-z0-9\-]{1,30})", re.IGNORECASE),
]

# TTS model — "set tts model to tts-1", "use model tts-1-hd".
_TTS_MODEL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:set|switch|use|change)\s+(?:tts\s+)?model\s*(?:to|=)?\s+([A-Za-z][A-Za-z0-9\-]{1,30})",
        re.IGNORECASE,
    ),
]

# Persona switch — "switch persona to tyler", "use persona default", "persona = max".
_PERSONA_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:switch|set|change|use|pick)\s+(?:my\s+)?persona\s*(?:to|=)?\s+([A-Za-z][A-Za-z0-9_\-]{1,30})",
        re.IGNORECASE,
    ),
    re.compile(r"\bpersona\s*[:=]\s*([A-Za-z][A-Za-z0-9_\-]{1,30})", re.IGNORECASE),
]


def _match_persona(text: str) -> str | None:
    for pat in _PERSONA_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip()
    return None


async def _apply_persona(chat_id: int, user_id: int, name: str) -> tuple[bool, str]:
    """Call switch_persona; return (ok, message). Never raises."""
    try:
        from navig.personas.manager import switch_persona

        cfg = await switch_persona(
            name,
            user_id=user_id,
            chat_id=chat_id,
            deliver_assets=False,
            bot_client=None,
        )
        return True, cfg.display_name or cfg.name
    except Exception as exc:
        logger.debug("nl_settings: persona switch failed: %s", exc)
        return False, str(exc)


def _apply_voice_replies(user_id: int, enabled: bool) -> None:
    """Persist voice_replies_enabled in the AudioConfig store."""
    try:
        from navig.gateway.channels.audio_menu.state import load_config, save_config

        cfg = load_config(user_id)
        cfg.voice_replies_enabled = enabled
        save_config(user_id, cfg)
    except Exception as exc:
        logger.debug("nl_settings: voice_replies persist failed: %s", exc)


def _apply_voice_name(user_id: int, voice: str) -> bool:
    """Persist a voice change in AudioConfig. Returns True if the value was set."""
    try:
        from navig.gateway.channels.audio_menu.state import load_config, save_config

        cfg = load_config(user_id)
        cfg.voice = voice
        save_config(user_id, cfg)
        return True
    except Exception as exc:
        logger.debug("nl_settings: voice persist failed: %s", exc)
        return False


def _apply_tts_model(user_id: int, model: str) -> bool:
    """Persist a TTS model change in AudioConfig."""
    try:
        from navig.gateway.channels.audio_menu.state import load_config, save_config

        cfg = load_config(user_id)
        cfg.model = model
        save_config(user_id, cfg)
        return True
    except Exception as exc:
        logger.debug("nl_settings: tts_model persist failed: %s", exc)
        return False


def _match_voice_replies(text: str) -> bool | None:
    """Return True/False/None for explicit voice-replies on/off, or None."""
    for pat in _VOICE_REPLIES_OFF_PATTERNS:
        if pat.search(text):
            return False
    for pat in _VOICE_REPLIES_ON_PATTERNS:
        if pat.search(text):
            return True
    return None


def _match_voice_name(text: str) -> str | None:
    for pat in _VOICE_NAME_PATTERNS:
        m = pat.search(text)
        if m:
            candidate = m.group(1).strip()
            # Filter out false positives like "voice = on" / "voice off" already
            # handled by the toggle patterns.
            if candidate.lower() in {"on", "off", "enabled", "disabled"}:
                return None
            return candidate
    return None


def _match_tts_model(text: str) -> str | None:
    for pat in _TTS_MODEL_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip()
    return None


async def try_handle_settings_intent(
    text: str,
    chat_id: int,
    user_id: int,
    channel: "TelegramChannel",
) -> bool:
    """Match `text` against settings-change patterns; apply + reply if matched.

    Returns True only when an intent was matched and handled (the caller should
    stop further dispatch). Returns False when no pattern matched.
    """
    if not text or not text.strip():
        return False

    stripped = text.strip()

    # Focus mode
    mode = _extract_focus_mode(stripped)
    if mode:
        await _apply_focus_mode(channel, chat_id, user_id, mode)
        label = mode if mode != "auto" else "auto (NAVIG decides)"
        await channel.send_message(
            chat_id,
            f"✅ Focus mode → {label}",
            parse_mode=None,
        )
        return True

    # Tier override
    tier = _extract_tier(stripped)
    if tier is not None:  # "" is a valid tier (auto)
        await _apply_tier(channel, chat_id, user_id, tier)
        await channel.send_message(
            chat_id,
            f"✅ Model tier → {_TIER_LABELS.get(tier, tier or 'auto')}",
            parse_mode=None,
        )
        return True

    # Voice replies on/off
    voice_replies = _match_voice_replies(stripped)
    if voice_replies is not None:
        _apply_voice_replies(user_id, voice_replies)
        await channel.send_message(
            chat_id,
            "🔊 Voice replies enabled." if voice_replies else "🔇 Voice replies disabled. Text only.",
            parse_mode=None,
        )
        return True

    # Voice name change
    voice = _match_voice_name(stripped)
    if voice:
        if _apply_voice_name(user_id, voice):
            await channel.send_message(
                chat_id, f"✅ Voice → {voice}", parse_mode=None
            )
        else:
            await channel.send_message(
                chat_id, "Couldn't save voice preference. Try the Deck → Account → Voice picker.",
                parse_mode=None,
            )
        return True

    # TTS model change
    tts_model = _match_tts_model(stripped)
    if tts_model:
        if _apply_tts_model(user_id, tts_model):
            await channel.send_message(
                chat_id, f"✅ TTS model → {tts_model}", parse_mode=None
            )
        else:
            await channel.send_message(
                chat_id, "Couldn't save TTS model. Try the Deck → Account → Voice picker.",
                parse_mode=None,
            )
        return True

    # Persona switch
    persona = _match_persona(stripped)
    if persona:
        ok, msg = await _apply_persona(chat_id, user_id, persona)
        if ok:
            await channel.send_message(
                chat_id, f"✅ Persona → {msg}", parse_mode=None
            )
        else:
            await channel.send_message(
                chat_id,
                f"Couldn't switch persona: {msg}. Try the Deck → Account → AI Persona picker.",
                parse_mode=None,
            )
        return True

    return False
