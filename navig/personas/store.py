"""Persona store — thin wrapper over RuntimeStore for persona persistence."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULT_PERSONA = "default"


def get_active_persona(user_id: int, chat_id: int | None = None) -> str:
    """Return the stored persona name for *user_id*.

    Falls back to ``"default"`` (NAVIG) when no state is recorded.
    """
    return get_persona_choice(user_id)[0]


def get_persona_choice(user_id: int) -> tuple[str, bool]:
    """Return ``(persona_name, explicitly_chosen)`` for *user_id*.

    ``explicitly_chosen`` is True only when the user picked the persona through
    :func:`set_active_persona` (``switch_persona`` — the deck picker, NL intent,
    ``/auto_start <name>``). No state → ``("default", False)``.
    """
    try:
        from navig.store.runtime import get_runtime_store  # noqa: PLC0415

        state = get_runtime_store().get_ai_state(user_id)
        if state and state.get("persona"):
            name = str(state["persona"]).strip() or _DEFAULT_PERSONA
            return name, bool(state.get("persona_explicit"))
    except Exception as exc:  # noqa: BLE001
        # A real store failure (locked/corrupt DB) — not the normal "no state yet"
        # path, which returns default without raising. Surface it so an operator can
        # see why a chosen persona silently reverted to default.
        logger.warning(
            "Could not read active persona for user %s; using default: %s", user_id, exc
        )
    return _DEFAULT_PERSONA, False


def set_active_persona(
    user_id: int, chat_id: int, persona_name: str, *, explicit: bool = True
) -> None:
    """Persist *persona_name* for *user_id* in the runtime store.

    ``explicit`` records that the user chose it (the default — this is the choice
    seam). A rollback restoring a previous persona passes that persona's own flag.
    """
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
            persona_explicit=explicit,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to persist persona '%s' for user %s: %s", persona_name, user_id, exc)
        raise
