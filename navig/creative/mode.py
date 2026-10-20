"""The one accessor for the canonical agent mode (``standard`` | ``creative``).

Every Creative-side gate (scan monitor, ``propose``/build/publish actions, route
checks) reads the mode through this module — never by poking the tracker directly —
so the gate set stays greppable and testable in one place.

Design rules:
- **Best-effort, never fatal:** a broken preference store must not take down a
  scan or a route; any failure degrades to ``standard`` (the safe default: no
  authoring, no proactive work).
- **Standard keeps discovery:** ``lookup``/``decide``/``status`` are available in
  both modes (owner mandate) — only *authoring* and *proactive proposal
  generation* check ``is_creative()``.
- **Mode never changes permissions:** this value gates what Creative *does*, never
  what tools are registered or how approvals run; tool schemas are byte-identical
  across modes.
"""

from __future__ import annotations

from navig.gateway.channels.focus_modes import DEFAULT_MODE

__all__ = ["DEFAULT_MODE", "get_agent_mode", "is_creative"]


def get_agent_mode() -> str:
    """Return the current canonical agent mode; ``standard`` on any failure."""
    try:
        from navig.agent.proactive.user_state import (
            UserPreferences,
            get_user_state_tracker,
        )

        value = get_user_state_tracker().get_preference(
            "chat_mode", DEFAULT_MODE
        )
        if value in UserPreferences._VALID_MODES:
            return str(value)
        # A stored value outside the vocabulary (pre-migration file read by a
        # surface that skipped the tracker's load path) still behaves as Standard.
        return DEFAULT_MODE
    except Exception:  # pragma: no cover - defensive: mode must never crash a caller
        return DEFAULT_MODE


def is_creative() -> bool:
    """True when Creative (capability intelligence) is armed."""
    return get_agent_mode() == "creative"
