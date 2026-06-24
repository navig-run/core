"""Credentials + settings for the MTProto user client.

Secrets (api_id, api_hash, the Telethon StringSession) live in the **vault** —
never in config.yaml, never on disk in plaintext, never logged. Non-secret
toggles (enabled, throttle) live in normal global config under ``telegram.user.*``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Vault labels (encrypted at rest).
VAULT_API_ID = "telegram_user_api_id"
VAULT_API_HASH = "telegram_user_api_hash"
VAULT_SESSION = "telegram_user_session"
VAULT_2FA = "telegram_user_2fa_password"

# Config keys (non-secret).
CFG_ENABLED = "telegram.user.enabled"
CFG_THROTTLE_EVERY = "telegram.user.throttle_every"
CFG_THROTTLE_SECS = "telegram.user.throttle_secs"


def _vault():
    from navig.vault import get_vault
    return get_vault()


def _vault_get(label: str) -> str | None:
    try:
        return _vault().get_bytes(label).decode("utf-8")
    except KeyError:
        return None
    except Exception:  # noqa: BLE001 — vault locked / decrypt error
        logger.debug("vault read failed for %s", label, exc_info=True)
        return None


def _vault_put(label: str, value: str) -> None:
    _vault().put(label, value.encode("utf-8"))


def _vault_del(label: str) -> None:
    try:
        _vault().delete(label)
    except Exception:  # noqa: BLE001
        pass


# ── API credentials ──────────────────────────────────────────────────────────


def get_api_id() -> int | None:
    raw = _vault_get(VAULT_API_ID)
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def get_api_hash() -> str | None:
    return _vault_get(VAULT_API_HASH)


def set_api_credentials(api_id: int | str, api_hash: str) -> None:
    """Persist api_id/api_hash to the vault (encrypted)."""
    _vault_put(VAULT_API_ID, str(int(api_id)))
    _vault_put(VAULT_API_HASH, api_hash.strip())


def have_api_credentials() -> bool:
    return get_api_id() is not None and bool(get_api_hash())


# ── Session (Telethon StringSession) ─────────────────────────────────────────


def get_session_string() -> str | None:
    return _vault_get(VAULT_SESSION)


def set_session_string(session: str) -> None:
    _vault_put(VAULT_SESSION, session)


def clear_session() -> None:
    _vault_del(VAULT_SESSION)


def get_2fa_password() -> str | None:
    """The stored Telegram 2FA (cloud) password, if the owner saved one."""
    return _vault_get(VAULT_2FA)


def is_logged_in() -> bool:
    return bool(get_session_string())


# ── Non-secret settings ──────────────────────────────────────────────────────


def _cfg():
    from navig.core import Config
    return Config()


def is_enabled() -> bool:
    try:
        return bool(_cfg().get(CFG_ENABLED, False))
    except Exception:  # noqa: BLE001
        return False


def set_enabled(value: bool) -> None:
    cfg = _cfg()
    cfg.set(CFG_ENABLED, bool(value), scope="global")
    cfg.save(scope="global")


def throttle() -> tuple[int, float]:
    """(sleep_every_N_messages, sleep_seconds) — flood-safe scan throttle."""
    cfg = _cfg()
    try:
        every = int(cfg.get(CFG_THROTTLE_EVERY, 200) or 200)
    except (TypeError, ValueError):
        every = 200
    try:
        secs = float(cfg.get(CFG_THROTTLE_SECS, 1.0) or 1.0)
    except (TypeError, ValueError):
        secs = 1.0
    return every, secs
