"""Persona store — thin wrapper over RuntimeStore for persona persistence."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULT_PERSONA = "default"


def get_active_persona(user_id: int, chat_id: int | None = None) -> str:
    """Return the stored persona name for *user_id*.

    Falls back to ``"default"`` when no state is recorded.
    """
    try:
        from navig.store.runtime import get_runtime_store  # noqa: PLC0415

        state = get_runtime_store().get_ai_state(user_id)
        if state and state.get("persona"):
            return str(state["persona"]).strip() or _DEFAULT_PERSONA
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read active persona for user %s: %s", user_id, exc)
    return _DEFAULT_PERSONA


def set_active_persona(user_id: int, chat_id: int, persona_name: str) -> None:
    """Persist *persona_name* for *user_id* in the runtime store."""
    try:
        from navig.store.runtime import get_runtime_store  # noqa: PLC0415

        store = get_runtime_store()
        # Preserve existing mode/context; only update persona field
        existing = store.get_ai_state(user_id) or {}
        store.set_ai_state(
            user_id=user_id,
            chat_id=chat_id,
            mode=existing.get("mode") or "active",
            persona=persona_name,
            context=existing.get("context"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to persist persona '%s' for user %s: %s", persona_name, user_id, exc)
        raise
