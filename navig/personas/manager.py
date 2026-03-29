"""Persona manager — atomic switch_persona() with rollback.

The switch is a single atomic operation: if any step fails the previous persona
is restored before surfacing the error to the caller.
"""
from __future__ import annotations

import logging
from pathlib import Path

from navig.personas.contracts import PersonaConfig

logger = logging.getLogger(__name__)


class PersonaSwitchError(RuntimeError):
    """Raised when a persona switch fails and rollback was performed."""


def get_active_persona_config(
    user_id: int,
    chat_id: int | None = None,
    cwd: Path | None = None,
) -> tuple[PersonaConfig, str]:
    """Return the current persona config + soul content for *user_id*."""
    from navig.personas.loader import load_persona  # noqa: PLC0415
    from navig.personas.store import get_active_persona  # noqa: PLC0415

    name = get_active_persona(user_id, chat_id)
    try:
        return load_persona(name, cwd=cwd)
    except Exception:  # noqa: BLE001
        # Fallback to default if the stored persona is broken
        return load_persona("default", cwd=cwd)


async def switch_persona(
    name: str,
    user_id: int,
    chat_id: int,
    cwd: Path | None = None,
    deliver_assets: bool = True,
    bot_client=None,
) -> PersonaConfig:
    """Switch to *name* persona atomically.  Rolls back on any failure.

    Parameters
    ----------
    name : str
        Target persona name.
    user_id, chat_id : int
        Telegram identifiers used for persistence and asset delivery.
    cwd : Path | None
        Working directory for project-local resolver chain.
    deliver_assets : bool
        When True, send wallpaper/sound via *bot_client* before returning.
    bot_client :
        Telegram bot client instance with ``send_photo`` / ``send_voice``.

    Returns
    -------
    PersonaConfig
        The successfully loaded persona config.

    Raises
    ------
    PersonaSwitchError
        If loading or persisting the persona fails; rollback to previous persona
        is attempted before raising.
    """
    from navig.personas.assets import deliver  # noqa: PLC0415
    from navig.personas.loader import load_persona  # noqa: PLC0415
    from navig.personas.resolver import resolve_persona  # noqa: PLC0415
    from navig.personas.store import get_active_persona, set_active_persona  # noqa: PLC0415

    # ── Step 1: save rollback point ───────────────────────────────────────────
    previous_name = get_active_persona(user_id, chat_id)

    # ── Step 2: validate persona exists ──────────────────────────────────────
    if resolve_persona(name, cwd=cwd) is None:
        raise PersonaSwitchError(
            f"Persona '{name}' not found. "
            "Run /personas to see available options."
        )

    try:
        # ── Step 3: load + validate config ───────────────────────────────────
        config, soul_content = load_persona(name, cwd=cwd)

        # ── Step 4: update ConversationalAgent (best-effort) ─────────────────
        try:
            from navig.agent.conversational import ConversationalAgent  # noqa: PLC0415

            # Update the global/singleton agent instance if accessible
            agent_instance = _get_agent_instance()
            if agent_instance is not None:
                agent_instance.set_active_persona(config, soul_content)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not update agent persona in-process: %s", exc)
            # Non-fatal: persona will be picked up on next agent construction

        # ── Step 5: persist ───────────────────────────────────────────────────
        set_active_persona(user_id, chat_id, name)

        # ── Step 6: deliver assets (wallpaper + sound) ────────────────────────
        if deliver_assets and bot_client is not None:
            await deliver(config, chat_id, bot_client, cwd=cwd)

    except Exception as exc:
        # ── Rollback ─────────────────────────────────────────────────────────
        try:
            set_active_persona(user_id, chat_id, previous_name)
            logger.info(
                "Persona switch to '%s' failed; rolled back to '%s'", name, previous_name
            )
        except Exception as rollback_exc:  # noqa: BLE001
            logger.error("Rollback failed for user %s: %s", user_id, rollback_exc)

        raise PersonaSwitchError(
            f"Failed to switch to persona '{name}': {exc}"
        ) from exc

    return config


def _get_agent_instance():
    """Try to retrieve the live ConversationalAgent instance from the gateway."""
    try:
        from navig.gateway.server import get_agent  # noqa: PLC0415

        return get_agent()
    except Exception:  # noqa: BLE001
        return None


def list_personas(cwd: Path | None = None) -> list[PersonaConfig]:
    """Return loaded PersonaConfig for all discovered personas."""
    from navig.personas.loader import load_persona  # noqa: PLC0415
    from navig.personas.resolver import discover_persona_paths  # noqa: PLC0415

    results = []
    for name in discover_persona_paths(cwd=cwd):
        try:
            config, _ = load_persona(name, cwd=cwd)
            results.append(config)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping persona '%s' (load error): %s", name, exc)
    return results
