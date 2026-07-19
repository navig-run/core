"""Single source of truth for NAVIG's focus modes (the ``/mode`` Telegram command).

A focus mode maps 1:1 to ``UserPreferences.chat_mode`` — the LIVE preference the agent
reads (``conversational_legacy``) and that gates do-not-disturb (``deep-focus`` / ``sleep``
→ notifications held, ``user_state.is_do_not_disturb``). The canonical vocabulary is
``UserPreferences._VALID_MODES``; this module owns the display metadata (emoji, label,
one-line description, confirmation) and the phrase→mode normalisation shared by ``/mode``,
the settings keyboard, and the natural-language intent.

Why it exists: ``/mode`` used to import ``navig.agent.soul.MOOD_REGISTRY`` — a registry that
was removed, so the command crashed on invocation. Worse, even before the crash it set
``chat_mode`` to mood ids like ``"balance"`` that are NOT in ``_VALID_MODES``, so
``set_preference`` rejected them (returns ``False``) and the mode never actually changed.
Keying every surface off ``_VALID_MODES`` — enforced by
``tests/gateway/channels/test_focus_modes.py`` — keeps the command, the keyboard, and the
NL intent speaking the one vocabulary the consumer validates against.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The mode a fresh install / ``/mode auto`` / ``/mode reset`` lands on. Matches
#: ``UserPreferences.chat_mode``'s default.
DEFAULT_MODE = "work"


@dataclass(frozen=True)
class FocusMode:
    """Display metadata for one focus mode. ``id`` is a canonical ``chat_mode`` value."""

    id: str
    emoji: str
    label: str
    description: str
    confirmation: str


# Keyed by the canonical chat_mode id. The key set MUST equal
# UserPreferences._VALID_MODES (guarded) so `/mode` can never offer a value the preference
# store will reject.
FOCUS_MODES: dict[str, FocusMode] = {
    "work": FocusMode(
        "work", "💼", "Work",
        "Balanced and task-ready — the default.",
        "💼 Work mode. Ready when you are.",
    ),
    "deep-focus": FocusMode(
        "deep-focus", "🎯", "Deep Focus",
        "Heads-down — non-urgent notifications are held.",
        "🎯 Deep focus. I'll hold non-urgent pings.",
    ),
    "planning": FocusMode(
        "planning", "🗺️", "Planning",
        "Thinking ahead and organising.",
        "🗺️ Planning mode.",
    ),
    "creative": FocusMode(
        "creative", "🎨", "Creative",
        "Looser and more exploratory.",
        "🎨 Creative mode.",
    ),
    "relax": FocusMode(
        "relax", "🌿", "Relax",
        "Casual and light.",
        "🌿 Relax mode.",
    ),
    "sleep": FocusMode(
        "sleep", "🌙", "Sleep",
        "Quiet hours — notifications are held.",
        "🌙 Sleep mode. Good night.",
    ),
}


# Free-form phrase → canonical mode. Every value MUST be a key of FOCUS_MODES (guarded).
# Legacy mood words (balance/balanced) resolve to the nearest live mode so old muscle
# memory still works; auto/reset/default clear back to DEFAULT_MODE.
_ALIASES: dict[str, str] = {
    "working": "work",
    "balance": "work",
    "balanced": "work",
    "deepfocus": "deep-focus",
    "deep focus": "deep-focus",
    "deep-work": "deep-focus",
    "deep work": "deep-focus",
    "deep": "deep-focus",
    "focus": "deep-focus",
    "plan": "planning",
    "planning mode": "planning",
    "create": "creative",
    "creative mode": "creative",
    "chill": "relax",
    "relax mode": "relax",
    "sleeping": "sleep",
    "night": "sleep",
    "sleep mode": "sleep",
    "auto": DEFAULT_MODE,
    "automatic": DEFAULT_MODE,
    "reset": DEFAULT_MODE,
    "default": DEFAULT_MODE,
}


def normalize(text: str) -> str | None:
    """Map a canonical id or a free-form phrase to a canonical mode id, or ``None``."""
    key = (text or "").strip().lower().rstrip(".,!?")
    if not key:
        return None
    if key in FOCUS_MODES:
        return key
    return _ALIASES.get(key)
