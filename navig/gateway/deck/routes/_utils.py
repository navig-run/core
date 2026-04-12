"""Shared helpers for Deck API route modules."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _get_vault():
    """Return the vault instance, or None if unavailable (best-effort)."""
    try:
        from navig.vault import get_vault

        return get_vault()
    except Exception as e:  # noqa: BLE001
        logger.debug("Vault not available: %s", e)
        return None
