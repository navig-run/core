"""
Feature detection and optional imports for Telegram channel modules.

This centralizes lazy import guards so `telegram.py` can stay focused on
channel orchestration while preserving the existing optional-dependency
behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# Inline keyboard system
ResponseKeyboardBuilder: Any = None
CallbackHandler: Any = None
get_callback_store: Callable[..., Any] | None = None
build_settings_keyboard: Callable[..., Any] | None = None
build_audio_keyboard: Callable[..., Any] | None = None
build_settings_hub_keyboard: Callable[..., Any] | None = None
_settings_header_text: Callable[..., str] | None = None
_audio_header_text: Callable[..., str] | None = None
_settings_hub_text: Callable[..., str] | None = None
try:
    from navig.gateway.channels.telegram_keyboards import (
        CallbackHandler,
        ResponseKeyboardBuilder,
        _audio_header_text,
        _settings_header_text,
        _settings_hub_text,
        build_audio_keyboard,
        build_settings_hub_keyboard,
        build_settings_keyboard,
        get_callback_store,
    )

    HAS_KEYBOARDS = True
except ImportError:
    HAS_KEYBOARDS = False

# Audio deep-menu module (provider → model → voice/speed/format)
_audio_screen_a_kb: Callable[..., Any] | None = None
_audio_screen_a_text: Callable[..., str] | None = None
_load_audio_config: Callable[..., Any] | None = None
try:
    from navig.gateway.channels.audio_menu import load_config as _load_audio_config
    from navig.gateway.channels.audio_menu import (
        screen_a_keyboard as _audio_screen_a_kb,
    )
    from navig.gateway.channels.audio_menu import screen_a_text as _audio_screen_a_text

    HAS_AUDIO_MENU = True
except ImportError:
    HAS_AUDIO_MENU = False

# Session management
get_session_manager: Callable[..., Any] | None = None
get_mention_gate: Callable[..., Any] | None = None
SessionManager: Any = None
MentionGate: Any = None
try:
    from navig.gateway.channels.telegram_sessions import (
        MentionGate,
        SessionManager,
        get_mention_gate,
        get_session_manager,
    )

    HAS_SESSIONS = True
except ImportError:
    HAS_SESSIONS = False

# Message templates
enforce_response_limits: Callable[..., Any] | None = None
try:
    from navig.gateway.channels.telegram_templates import enforce_response_limits

    HAS_TEMPLATES = True
except ImportError:
    HAS_TEMPLATES = False

# Decoy responder for unauthorized users
generate_decoy: Callable[..., Any] | None = None
try:
    from navig.gateway.decoy_responder import generate as generate_decoy

    HAS_DECOY = True
except ImportError:
    HAS_DECOY = False

# Cinematic pipeline renderer
StatusRenderer: Any = None
try:
    from navig.gateway.channels.telegram_renderer import StatusRenderer

    HAS_RENDERER = True
except ImportError:
    HAS_RENDERER = False

# Mode classifier
classify_mode: Callable[[str], str] | None = None
mode_to_llm_tier: Callable[..., Any] | None = None
select_tools_for_text: Callable[..., Any] | None = None
extract_url: Callable[..., Any] | None = None
try:
    from navig.gateway.channels.telegram_mode_classifier import (
        classify_mode,
        extract_url,
        mode_to_llm_tier,
        select_tools_for_text,
    )

    HAS_CLASSIFIER = True
except ImportError:
    HAS_CLASSIFIER = False

# Voice STT/TTS pipeline
try:
    from navig.voice.stt import STT as _STT
    from navig.voice.stt import STTConfig as _STTConfig
    from navig.voice.stt import STTProvider as _STTProvider
    from navig.voice.tts import TTS as _TTS
    from navig.voice.tts import TTSConfig as _TTSConfig
    from navig.voice.tts import TTSProvider as _TTSProvider

    HAS_VOICE = True
except ImportError:
    _STT = None
    _STTProvider = None
    _STTConfig = None
    _TTS = None
    _TTSProvider = None
    _TTSConfig = None
    HAS_VOICE = False

_FEATURE_FLAGS = {
    "keyboards": HAS_KEYBOARDS,
    "audio_menu": HAS_AUDIO_MENU,
    "sessions": HAS_SESSIONS,
    "templates": HAS_TEMPLATES,
    "decoy": HAS_DECOY,
    "renderer": HAS_RENDERER,
    "classifier": HAS_CLASSIFIER,
    "voice": HAS_VOICE,
}

TELEGRAM_FEATURES = frozenset(
    name for name, enabled in _FEATURE_FLAGS.items() if enabled
)


class TelegramFeaturesMixin:
    """Shared feature registry for Telegram channel mixins."""

    _features: frozenset[str] = TELEGRAM_FEATURES

    def _has_feature(self, name: str) -> bool:
        return name in self._features


__all__ = [
    "CallbackHandler",
    "MentionGate",
    "ResponseKeyboardBuilder",
    "SessionManager",
    "StatusRenderer",
    "TELEGRAM_FEATURES",
    "TelegramFeaturesMixin",
    "_STT",
    "_STTConfig",
    "_STTProvider",
    "_TTS",
    "_TTSConfig",
    "_TTSProvider",
    "_audio_header_text",
    "_audio_screen_a_kb",
    "_audio_screen_a_text",
    "_load_audio_config",
    "_settings_header_text",
    "_settings_hub_text",
    "build_audio_keyboard",
    "build_settings_hub_keyboard",
    "build_settings_keyboard",
    "classify_mode",
    "enforce_response_limits",
    "extract_url",
    "generate_decoy",
    "get_callback_store",
    "get_mention_gate",
    "get_session_manager",
    "mode_to_llm_tier",
    "select_tools_for_text",
    "HAS_AUDIO_MENU",
    "HAS_CLASSIFIER",
    "HAS_DECOY",
    "HAS_KEYBOARDS",
    "HAS_RENDERER",
    "HAS_SESSIONS",
    "HAS_TEMPLATES",
    "HAS_VOICE",
]
