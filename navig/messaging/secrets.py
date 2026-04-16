from __future__ import annotations

import logging
import os
from typing import Any

from navig import console_helper as ch
from navig.core.file_permissions import set_owner_only_file_permissions
from navig.platform.paths import config_dir

logger = logging.getLogger(__name__)


_TELEGRAM_VAULT_LABELS = (
    "telegram/bot_token",
    "telegram/token",
    "telegram/bot-token",
    "telegram_bot_token",
)


def _resolve_telegram_token_from_vault() -> str:
    try:
        from navig.vault.core import get_vault

        vault = get_vault()
        for label in _TELEGRAM_VAULT_LABELS:
            try:
                value = vault.get_secret(label)
            except Exception:
                continue
            value = (value or "").strip()
            if value:
                return value
    except Exception:
        pass  # best-effort: vault unavailable or not configured
    return ""


def _resolve_telegram_token_from_legacy_store() -> str:
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
        pass  # best-effort: vault unavailable or not configured
    return ""


def _resolve_telegram_token_from_env_file() -> str:
    """Read TELEGRAM_BOT_TOKEN / NAVIG_TELEGRAM_BOT_TOKEN from ~/.navig/.env.

    The .env file is not loaded into ``os.environ`` at CLI startup, so
    ``os.getenv()`` returns ``None`` even when the token is present there.
    This helper reads the file directly and returns the value.
    """

    env_file = config_dir() / ".env"
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
        pass  # best-effort: .env file unreadable; skip this resolution path
    return ""


def resolve_telegram_bot_token(raw_config: dict[str, Any] | None = None) -> str:
    """Resolve Telegram bot token with vault-first policy and compatibility fallbacks.

    Resolution order:
    1) Vault labels (telegram/bot_token, telegram/token, ...)
    2) Legacy provider credential (telegram token/bot_token)
    3) NAVIG_TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_TOKEN environment variable
    4) ~/.navig/.env file (NAVIG_TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN)
    5) telegram.bot_token in provided config
    6) telegram.bot_token from global config manager
    """
    token = _resolve_telegram_token_from_vault()
    if token:
        return token

    token = _resolve_telegram_token_from_legacy_store()
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


# ── Telegram user-identity resolution ─────────────────────────────────────────

_TELEGRAM_UID_VAULT_LABELS = (
    "telegram/user_id",
    "telegram.user_id",
    "telegram_user_id",
)


def _resolve_telegram_uid_from_vault() -> str | None:
    try:
        from navig.vault.core import get_vault

        vault = get_vault()
        for label in _TELEGRAM_UID_VAULT_LABELS:
            try:
                value = vault.get_secret(label)
            except Exception:
                continue
            value = (value or "").strip()
            if value:
                return value
    except Exception:
        pass  # best-effort: vault unavailable or not configured
    return None


def _resolve_telegram_uid_from_legacy_store() -> str | None:
    try:
        from navig.vault import get_vault

        vault = get_vault()
        for key in ("user_id", "uid"):
            secret = vault.get_secret("telegram", key, caller="messaging.telegram_uid")
            if secret:
                uid = (secret.reveal() or "").strip()
                if uid:
                    return uid
    except Exception:
        pass  # best-effort: vault unavailable or not configured
    return None


def _resolve_telegram_uid_from_env_file() -> str | None:
    """Read NAVIG_TELEGRAM_UID from ~/.navig/.env (may not be in os.environ at CLI startup)."""

    env_file = config_dir() / ".env"
    if not env_file.exists():
        return None
    try:
        text = env_file.read_bytes().lstrip(b"\xef\xbb\xbf").decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key == "NAVIG_TELEGRAM_UID" and value:
                return value
    except Exception:
        pass  # best-effort: .env file unreadable; skip this resolution path
    return None


def resolve_telegram_uid(raw_config: dict[str, Any] | None = None) -> str | None:
    """Resolve the owner's Telegram user ID with vault-first policy.

    Resolution order:
    1) Vault labels (telegram/user_id, telegram.user_id, telegram_user_id)
    2) Legacy provider credential (telegram user_id / uid)
    3) NAVIG_TELEGRAM_UID environment variable
    4) ~/.navig/.env file (NAVIG_TELEGRAM_UID)
    5) telegram.user_id in provided config  (deprecated — emits warning)
    6) telegram.user_id from global config manager  (deprecated — emits warning)

    Returns ``None`` when no UID is configured so callers can distinguish
    "missing" from an empty string.
    """
    uid = _resolve_telegram_uid_from_vault()
    if uid:
        return uid

    uid = _resolve_telegram_uid_from_legacy_store()
    if uid:
        return uid

    uid = (os.getenv("NAVIG_TELEGRAM_UID") or "").strip()
    if uid:
        return uid

    uid = _resolve_telegram_uid_from_env_file()
    if uid:
        return uid

    if raw_config and isinstance(raw_config, dict):
        telegram_cfg = raw_config.get("telegram", {})
        if isinstance(telegram_cfg, dict):
            uid = str(telegram_cfg.get("user_id") or "").strip()
            if uid:
                logger.warning(
                    "telegram.user_id read from config (deprecated). "
                    "Store the UID in the vault instead: "
                    "navig vault set telegram.user_id <uid>"
                )
                return uid

    try:
        from navig.config import get_config_manager

        cfg = get_config_manager().global_config or {}
        telegram_cfg = cfg.get("telegram", {}) if isinstance(cfg, dict) else {}
        if isinstance(telegram_cfg, dict):
            uid = str(telegram_cfg.get("user_id") or "").strip()
            if uid:
                logger.warning(
                    "telegram.user_id read from config.yaml (deprecated). "
                    "Store the UID in the vault instead: "
                    "navig vault set telegram.user_id <uid>"
                )
                return uid
    except Exception:
        pass  # best-effort: config manager unavailable or global config not loaded

    return None


def ensure_telegram_uid(
    vault: Any = None,
    raw_config: dict[str, Any] | None = None,
) -> str:
    """Return the owner's Telegram user ID, prompting once on first run.

    In headless / CI mode (no TTY or ``CI`` env var set) a
    :class:`RuntimeError` is raised when no UID is found — the operator must
    set ``NAVIG_TELEGRAM_UID`` in the environment.

    Vault write failure is treated as fatal: the caller must not proceed with
    an unsaved UID.
    """
    import sys

    uid = resolve_telegram_uid(raw_config)
    if uid:
        return uid

    headless = not sys.stdin.isatty() or bool(os.getenv("CI"))
    if headless:
        raise RuntimeError(
            "Telegram user ID not configured. "
            "Set via: NAVIG_TELEGRAM_UID env var or "
            "navig vault set telegram.user_id <uid>"
        )

    # Interactive first-time setup
    ch.info("Telegram user ID not found. This is a one-time setup.")
    ch.dim("(Find your ID by messaging @userinfobot on Telegram)")
    uid = input("Enter your Telegram user ID: ").strip()

    if not uid.isdigit():
        raise ValueError("Telegram user ID must be a numeric string.")

    # Persist to vault (fatal on failure — do not continue with unsaved UID)
    try:
        import json as _json

        from navig.vault.core import get_vault

        _vault = vault if vault is not None else get_vault()
        if _vault is None:
            raise RuntimeError("Vault not available.")
        _vault.put("telegram/user_id", _json.dumps({"value": uid}).encode())
    except Exception as exc:
        raise RuntimeError(
            f"Failed to save Telegram user ID to vault: {exc}. "
            "Fix the vault and try again — the UID has NOT been saved."
        ) from exc

    # Best-effort: also write to ~/.navig/.env for env-based fallback access
    try:
        from pathlib import Path as _Path  # noqa: F401 (kept for symmetry)

        env_file = config_dir() / ".env"
        existing = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
        lines = [ln for ln in existing.splitlines() if not ln.startswith("NAVIG_TELEGRAM_UID=")]
        lines.append(f"NAVIG_TELEGRAM_UID={uid}")
        atomic_write_text(env_file, "\n".join(lines) + "\n")
        set_owner_only_file_permissions(env_file)
    except Exception:
        pass  # .env write is best-effort; vault write succeeded above

    ch.success("\u2713 Saved to vault. You will not be asked again.")
    return uid
