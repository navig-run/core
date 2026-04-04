from __future__ import annotations

import os
from typing import Any

from navig.messaging.provider import IMessagingProvider
from navig.messaging.secrets import resolve_telegram_bot_token, resolve_telegram_uid

_SUPPORTED_PROVIDER_NAMES = frozenset({"telegram", "none"})


class TelegramProvider:
    """Telegram implementation of :class:`IMessagingProvider`."""

    @property
    def name(self) -> str:
        return "telegram"

    def is_enabled(self, raw_config: dict[str, Any]) -> bool:
        cfg = _telegram_config(raw_config)
        return bool(cfg.get("bot_token"))

    def create_channel(self, gateway: Any, provider_config: dict[str, Any]) -> Any | None:
        from navig.gateway.channels.telegram import create_telegram_channel

        return create_telegram_channel(gateway, provider_config)


def _messaging_cfg(raw_config: dict[str, Any]) -> dict[str, Any]:
    cfg = raw_config or {}
    messaging = cfg.get("messaging", {}) if isinstance(cfg, dict) else {}
    return messaging if isinstance(messaging, dict) else {}


def _telegram_config(raw_config: dict[str, Any]) -> dict[str, Any]:
    cfg = raw_config or {}
    telegram_cfg = cfg.get("telegram", {}) if isinstance(cfg, dict) else {}
    telegram_cfg = telegram_cfg if isinstance(telegram_cfg, dict) else {}

    token = resolve_telegram_bot_token(cfg)
    owner_uid = resolve_telegram_uid(cfg)
    return {
        "bot_token": token,
        "owner_uid": owner_uid,
        "allowed_users": telegram_cfg.get("allowed_users", []),
        "allowed_groups": telegram_cfg.get("allowed_groups", []),
        "require_auth": telegram_cfg.get("require_auth", True),
    }


def get_active_provider_name(raw_config: dict[str, Any]) -> str:
    """Resolve active messaging provider from env/config.

    Resolution order:
    1) ``NAVIG_MESSAGING_PROVIDER``
    2) ``messaging.provider``
    3) default: ``telegram`` (backward-compatible)
    """

    env_name = (os.getenv("NAVIG_MESSAGING_PROVIDER") or "").strip().lower()
    if env_name:
        return env_name

    messaging = _messaging_cfg(raw_config)
    cfg_name = str(messaging.get("provider", "")).strip().lower()
    if cfg_name:
        return cfg_name

    return "telegram"


def is_supported_provider_name(name: str) -> bool:
    return (name or "").strip().lower() in _SUPPORTED_PROVIDER_NAMES


def supported_provider_names() -> tuple[str, ...]:
    return tuple(sorted(_SUPPORTED_PROVIDER_NAMES))


def _provider_for(name: str) -> IMessagingProvider | None:
    if name == "telegram":
        return TelegramProvider()
    return None


def is_provider_enabled(provider_name: str, raw_config: dict[str, Any]) -> bool:
    """True when the requested provider is active and has usable config."""

    active_name = get_active_provider_name(raw_config)
    if active_name != provider_name:
        return False

    provider = _provider_for(provider_name)
    if provider is None:
        return False
    return provider.is_enabled(raw_config)


def create_channel_for_provider(
    provider_name: str,
    gateway: Any,
    raw_config: dict[str, Any],
) -> Any | None:
    """Create channel instance for a provider name.

    Returns ``None`` for disabled or unsupported providers.
    """

    provider = _provider_for(provider_name)
    if provider is None:
        return None

    if provider_name == "telegram":
        cfg = _telegram_config(raw_config)
    else:
        cfg = {}

    if not provider.is_enabled(raw_config):
        return None
    return provider.create_channel(gateway, cfg)
