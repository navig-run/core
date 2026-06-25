"""Credential lookup for social publishers.

Tokens are keyed by the same ``vaultProvider`` name the deck Settings UI uses
(``twitter``, ``linkedin``, ``reddit``, ``facebook``, ``instagram``,
``youtube``, …). Resolution order: environment variable → vault. Env first so
tests and power users can override without touching the vault.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def get_token(provider: str) -> str | None:
    """Return the access token/secret for *provider*, or None."""
    for env in (
        f"NAVIG_{provider.upper()}_TOKEN",
        f"{provider.upper()}_TOKEN",
        f"{provider.upper()}_ACCESS_TOKEN",
    ):
        val = os.environ.get(env)
        if val:
            return val
    try:
        from navig.vault.core import get_vault

        secret = get_vault().get_secret(provider)
        val = secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret)
        return val or None
    except Exception:  # noqa: BLE001 — missing item / locked vault → not configured
        return None


def get_config(provider: str, key: str) -> Any | None:
    """Return a non-secret config field (e.g. a page id, subreddit) for *provider*.

    Reads ``adapters.social.<provider>.<key>`` from global config, falling back
    to ``NAVIG_<PROVIDER>_<KEY>`` env.
    """
    env = os.environ.get(f"NAVIG_{provider.upper()}_{key.upper()}")
    if env:
        return env
    try:
        from navig.config import get_config_manager

        cfg = (get_config_manager().global_config or {}).get("adapters", {}).get("social", {})
        return (cfg.get(provider, {}) or {}).get(key)
    except Exception:  # noqa: BLE001
        return None
