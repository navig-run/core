"""Persona assets — resolve wallpaper/sound paths and deliver via Telegram.

Asset delivery errors are logged and swallowed: a missing wallpaper or
broken sound file must never block a persona switch.
"""
from __future__ import annotations

import logging
from pathlib import Path

from navig.personas.contracts import PersonaConfig

logger = logging.getLogger(__name__)


def _resolve_asset(relative_path: str, persona_dir: Path | None) -> Path | None:
    """Resolve a relative asset path against the persona directory.

    Returns ``None`` if the file does not exist.
    """
    if not relative_path or persona_dir is None:
        return None
    candidate = persona_dir / relative_path
    return candidate if candidate.is_file() else None


async def deliver(
    config: PersonaConfig,
    chat_id: int,
    bot_client,
    cwd: Path | None = None,
) -> None:
    """Send wallpaper (photo) and startup_sound (voice) to *chat_id*.

    Both sends are fire-and-forget: failure is logged but never raised.
    Assets are sent *before* the caller sends text confirmation.
    """
    from navig.personas.resolver import resolve_persona  # noqa: PLC0415

    persona_dir = resolve_persona(config.name, cwd=cwd)

    if config.wallpaper:
        path = _resolve_asset(config.wallpaper, persona_dir)
        if path:
            try:
                with open(path, "rb") as f:
                    await bot_client.send_photo(chat_id=chat_id, photo=f)
                logger.debug("Delivered wallpaper for persona '%s'", config.name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to send wallpaper for persona '%s': %s", config.name, exc
                )
        else:
            logger.debug(
                "Wallpaper '%s' for persona '%s' not found; skipping",
                config.wallpaper,
                config.name,
            )

    if config.startup_sound:
        path = _resolve_asset(config.startup_sound, persona_dir)
        if path:
            try:
                with open(path, "rb") as f:
                    await bot_client.send_voice(chat_id=chat_id, voice=f)
                logger.debug("Delivered startup sound for persona '%s'", config.name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to send startup sound for persona '%s': %s", config.name, exc
                )
        else:
            logger.debug(
                "Startup sound '%s' for persona '%s' not found; skipping",
                config.startup_sound,
                config.name,
            )
