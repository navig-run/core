from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


_TELEGRAM_VAULT_LABELS = (
    "telegram/bot_token",
    "telegram/token",
    "telegram/bot-token",
    "telegram_bot_token",
)


def _resolve_telegram_token_from_vault_v2() -> str:
    try:
        from navig.vault.core_v2 import get_vault_v2

        vault = get_vault_v2()
        for label in _TELEGRAM_VAULT_LABELS:
            try:
                value = vault.get_secret(label)
            except Exception:
                continue
            value = (value or "").strip()
            if value:
                return value
    except Exception:
        pass
    return ""


def _resolve_telegram_token_from_vault_v1() -> str:
    try:
        from navig.vault import get_vault

        vault = get_vault()
        for key in ("token", "bot_token", "api_key"):
            secret = vault.get_secret("telegram", key, caller="messaging.telegram_token")
            if secret:
                token = (secret.reveal() or "").strip()
                if token:
                    return token

        # Fallback: direct provider scan in case profile/key resolution misses
        # a valid telegram credential shape.
        try:
            credentials = vault.list(provider="telegram")
        except Exception:
            credentials = []

        for info in credentials:
            if not getattr(info, "enabled", True):
                continue
            credential_id = str(getattr(info, "id", "") or "").strip()
            if not credential_id:
                continue
            try:
                cred = vault.get_by_id(credential_id, caller="messaging.telegram_token")
            except TypeError:
                cred = vault.get_by_id(credential_id)
            except Exception:
                continue
            if not cred:
                continue
            data = getattr(cred, "data", {}) or {}
            for key in ("bot_token", "token", "api_key", "value"):
                value = str(data.get(key) or "").strip()
                if value:
                    return value
    except Exception:
        pass
    return ""


def _resolve_telegram_token_from_env_file() -> str:
    """Read TELEGRAM_BOT_TOKEN / NAVIG_TELEGRAM_BOT_TOKEN from ~/.navig/.env.

    The .env file is not loaded into ``os.environ`` at CLI startup, so
    ``os.getenv()`` returns ``None`` even when the token is present there.
    This helper reads the file directly and returns the value.
    """
    from pathlib import Path

    env_file = Path.home() / ".navig" / ".env"
    if not env_file.exists():
        return ""
    try:
        # Strip BOM (\xef\xbb\xbf) that Windows editors sometimes write
        text = env_file.read_bytes().lstrip(b"\xef\xbb\xbf").decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key in ("NAVIG_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN") and value:
                return value
    except Exception:
        pass
    return ""


def resolve_telegram_bot_token(raw_config: dict[str, Any] | None = None) -> str:
    """Resolve Telegram bot token with vault-first policy and compatibility fallbacks.

    Resolution order:
    1) Vault v2 labels (telegram/bot_token, telegram/token, ...)
    2) Vault v1 provider credential (telegram token/bot_token)
    3) NAVIG_TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_TOKEN environment variable
    4) ~/.navig/.env file (NAVIG_TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN)
    5) telegram.bot_token in provided config
    6) telegram.bot_token from global config manager
    """
    token = _resolve_telegram_token_from_vault_v2()
    if token:
        return token

    token = _resolve_telegram_token_from_vault_v1()
    if token:
        return token

    token = (os.getenv("NAVIG_TELEGRAM_BOT_TOKEN") or "").strip()
    if token:
        return token

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if token:
        return token

    token = _resolve_telegram_token_from_env_file()
    if token:
        return token

    if raw_config and isinstance(raw_config, dict):
        telegram_cfg = raw_config.get("telegram", {})
        if isinstance(telegram_cfg, dict):
            token = str(telegram_cfg.get("bot_token") or "").strip()
            if token:
                logger.warning(
                    "telegram.bot_token read from config (deprecated). "
                    "Store the token in the vault instead: "
                    "navig vault set telegram_bot_token <token>"
                )
                return token

    try:
        from navig.config import get_config_manager

        cfg = get_config_manager().global_config or {}
        telegram_cfg = cfg.get("telegram", {}) if isinstance(cfg, dict) else {}
        if isinstance(telegram_cfg, dict):
            token = str(telegram_cfg.get("bot_token") or "").strip()
            if token:
                logger.warning(
                    "telegram.bot_token read from config.yaml (deprecated). "
                    "Store the token in the vault instead: "
                    "navig vault set telegram_bot_token <token>"
                )
                return token
    except Exception:
        return ""

    return ""
