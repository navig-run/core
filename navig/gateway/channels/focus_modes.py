"""Single source of truth for NAVIG's agent modes (the ``/mode`` Telegram command).

An agent mode maps 1:1 to ``UserPreferences.chat_mode`` — the LIVE preference every
surface reads and the agent runtime shows in its session context. The vocabulary is
exactly two values (owner decision 2026-10-20):

- ``standard`` — everyday assistance, coding, operations, research, execution (default).
- ``creative`` — proactive discovery, capability composition, intelligent improvements,
  selective creation.

The six retired *focus modes* (work / deep-focus / planning / creative / relax / sleep)
are legacy input only: they normalize to ``standard`` (see ``resolve``) and their stored
values migrate on load (``UserStateTracker._migrate_agent_mode``). The OLD tone meaning
of ``creative`` never maps to the new capability mode — a retired alias can only land on
``standard``; only the literal current word ``creative`` selects Creative.

This module owns the display metadata (emoji, label, one-line description,
confirmation) and the phrase→mode normalisation shared by ``/mode``, the settings
keyboard, and the natural-language intent. The canonical vocabulary that the preference
store *validates* is ``UserPreferences._VALID_MODES``; the guard test
``tests/gateway/test_focus_modes.py`` pins the two together, so no surface can offer a
value ``set_preference`` would reject.

Why the module name survives: ``focus_modes`` is the import path of record (its importers
are updated tree-wide in the same change — synaptic-change law), but everything inside
is named for agent modes; ``FOCUS_MODES``/``FocusMode`` remain aliases for one release.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The mode a fresh install / ``/mode auto`` / ``/mode reset`` lands on. Matches
#: ``UserPreferences.chat_mode``'s default.
DEFAULT_MODE = "standard"


@dataclass(frozen=True)
class AgentMode:
    """Display metadata for one agent mode. ``id`` is a canonical ``chat_mode`` value."""

    id: str
    emoji: str
    label: str
    description: str
    confirmation: str


# Keyed by the canonical chat_mode id. The key set MUST equal
# UserPreferences._VALID_MODES (guarded) so `/mode` can never offer a value the
# preference store will reject.
AGENT_MODES: dict[str, AgentMode] = {
    "standard": AgentMode(
        "standard", "💼", "Standard",
        "Everyday assistance, coding, operations, research and execution.",
        "💼 Standard mode. Ready when you are.",
    ),
    "creative": AgentMode(
        "creative", "🎨", "Creative",
        "Proactive discovery, capability composition, intelligent improvements, selective creation.",
        "🎨 Creative mode. Capability intelligence is on.",
    ),
}

#: Release alias — the old names import cleanly until the tree-wide rename lands
#: in a later release. Same object, not a copy.
FOCUS_MODES: dict[str, AgentMode] = AGENT_MODES
FocusMode = AgentMode


# Free-form phrase → canonical mode for NON-retired vocabulary only. Every value MUST
# be a key of AGENT_MODES (guarded). These are the reset words: they land on the
# default without any "retired" notice.
_ALIASES: dict[str, str] = {
    "auto": DEFAULT_MODE,
    "automatic": DEFAULT_MODE,
    "reset": DEFAULT_MODE,
    "default": DEFAULT_MODE,
}


# Retired focus-mode ids and phrases → standard. `resolve()` reports these as legacy
# so surfaces can say the mode retired WITHOUT listing the obsolete choices. Nothing
# here may map to "creative": no mood phrase can silently enable capability authoring.
_LEGACY_ALIASES: dict[str, str] = dict.fromkeys(
    (
        "work",
        "working",
        "balance",
        "balanced",
        "deep-focus",
        "deepfocus",
        "deep focus",
        "deep-work",
        "deep work",
        "deep",
        "focus",
        "plan",
        "planning",
        "planning mode",
        "chill",
        "relax",
        "relax mode",
        "sleep",
        "sleeping",
        "sleep mode",
        "night",
    ),
    "standard",
)


def resolve(text: str) -> tuple[str | None, bool]:
    """Map a canonical id or free-form phrase to ``(canonical_id | None, was_legacy)``.

    ``was_legacy`` is True only for a retired-mode phrase — the caller (``/mode``,
    ``POST /api/deck/mode``, the NL intent) uses it to answer "that mode has retired —
    NAVIG now has Standard and Creative" while still behaving as ``standard``.
    """
    key = (text or "").strip().lower().rstrip(".,!?")
    if not key:
        return None, False
    if key in AGENT_MODES:
        # The literal current words only — "create" is deliberately NOT here.
        return key, False
    if key in _ALIASES:
        return _ALIASES[key], False
    if key in _LEGACY_ALIASES:
        return _LEGACY_ALIASES[key], True
    return None, False


def normalize(text: str) -> str | None:
    """Map a canonical id or a free-form phrase to a canonical mode id, or ``None``."""
    return resolve(text)[0]


def is_legacy_request(text: str) -> bool:
    """True when ``text`` named a retired focus mode (the caller should say so)."""
    return resolve(text)[1]
