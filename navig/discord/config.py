"""Credentials for the Discord account manager.

Both tokens live in the **vault** — never in config.yaml, never logged. The bot token
label is shared with ``navig init --messaging`` and the Discord messaging adapter
(``adapters.discord.bot_token: vault:discord_bot_token``) and with the gateway's
channel registry, which checks the same label.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

logger = logging.getLogger(__name__)

Identity = Literal["bot", "account"]

VAULT_BOT_TOKEN = "discord_bot_token"
VAULT_USER_TOKEN = "discord_user_token"

_LABELS: dict[str, str] = {"bot": VAULT_BOT_TOKEN, "account": VAULT_USER_TOKEN}


def _vault():
    from navig.vault import get_vault

    return get_vault()


def vault_unavailable_reason() -> str | None:
    """Why the vault cannot be opened at all, or None when it can.

    A missing vault engine is not a missing token: reporting "not set" over it would
    send the owner to re-enter credentials that were stored all along.
    """
    try:
        _vault()
    except ImportError as exc:
        return str(exc)
    except Exception:  # noqa: BLE001 — locked/other: per-label reads report it
        return None
    return None


def get_token(identity: Identity) -> str:
    """The stored token for *identity*, or ``""`` when absent/unreadable."""
    from navig.vault import reveal_secret

    try:
        vault = _vault()
    except Exception:  # noqa: BLE001 — reported separately by vault_unavailable_reason
        return ""
    return reveal_secret(vault, _LABELS[identity])


def set_token(identity: Identity, token: str) -> None:
    """Store *token* for *identity* (same ``{"value": …}`` shape as the messaging wizard)."""
    token = token.strip()
    if not token:
        raise ValueError("empty token")
    _vault().put(_LABELS[identity], json.dumps({"value": token}).encode("utf-8"))


def clear_token(identity: Identity) -> bool:
    """Remove the token for *identity*. Returns True when something was deleted."""
    try:
        return bool(_vault().delete(_LABELS[identity]))
    except KeyError:
        return False


def have_token(identity: Identity) -> bool:
    return bool(get_token(identity))
