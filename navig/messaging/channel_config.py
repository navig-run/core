"""configured_channels — the shared, config-only answer to "which messaging
channels have credentials/config present".

Single source of truth for status surfaces (the TUI Gateway badge, `navig gateway
status`) so they can never disagree. Reads config (+ the vault for the Telegram
token, via the canonical resolver) and does NO network I/O, so it is safe to call
from a synchronous status resolver.

Mirrors the per-channel config checks in `navig gateway status`
(commands/gateway.py). **Add a channel there → add it here.**
"""

from __future__ import annotations

from typing import Any


def configured_channels(raw_config: dict[str, Any] | None = None) -> list[str]:
    """Display names of gateway messaging channels that are configured.

    Config-only (no reachability probes). Order matches `navig gateway status`:
    Telegram, Matrix, Discord, WhatsApp, Email.

    Args:
        raw_config: the global config dict; loaded from the Config singleton when
            omitted.
    """
    if raw_config is None:
        try:
            from navig.core import Config

            raw_config = Config().global_config or {}
        except Exception:  # noqa: BLE001
            raw_config = {}
    if not isinstance(raw_config, dict):
        raw_config = {}

    names: list[str] = []

    # Telegram — vault-first canonical resolver (vault → legacy → env → config).
    try:
        from navig.messaging.secrets import resolve_telegram_bot_token

        if resolve_telegram_bot_token(raw_config):
            names.append("Telegram")
    except Exception:  # noqa: BLE001
        pass

    # Matrix — comms.matrix or a top-level matrix section.
    mx_cfg = (raw_config.get("comms") or {}).get("matrix") or raw_config.get("matrix") or {}
    if isinstance(mx_cfg, dict) and mx_cfg.get("access_token"):
        names.append("Matrix")

    # Discord.
    dc_cfg = raw_config.get("discord") or {}
    if isinstance(dc_cfg, dict) and (dc_cfg.get("bot_token") or dc_cfg.get("token")):
        names.append("Discord")

    # WhatsApp (mautrix bridge).
    wa_cfg = raw_config.get("whatsapp") or (raw_config.get("bridges") or {}).get("whatsapp") or {}
    if isinstance(wa_cfg, dict) and (wa_cfg.get("enabled") or wa_cfg.get("WHATSAPP_ENABLED")):
        names.append("WhatsApp")

    # Email / SMTP.
    em_cfg = raw_config.get("email") or raw_config.get("smtp") or {}
    if isinstance(em_cfg, dict) and (
        em_cfg.get("smtp_host") or em_cfg.get("SMTP_HOST") or em_cfg.get("host")
    ):
        names.append("Email")

    return names
