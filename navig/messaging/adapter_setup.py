"""Build a messaging adapter from config — in ANY process, not only the gateway.

The daemon runs two processes: ``navig gateway start`` and the Telegram worker
(see ``daemon/telegram_worker.py``). Only the gateway ran
``_init_messaging_adapters``, so in the worker — which serves the Deck on 8789
AND delivers about half of all due reminders — the adapter registry was empty.
Measured: ``_send_one("sms", ...)`` there returned ``"sms adapter not enabled"``,
so an appointment reminder delivered by the worker silently lost its SMS copy,
``navig notify send appointment`` from a terminal could never text, and the
Mini App showed SMS as "not configured" on an install where it worked.

This module is the one place that knows how to construct the stateless HTTP
senders from ``adapters.<name>`` config. The gateway's boot uses it, and the
notify router calls :func:`ensure_registered` when a send finds the adapter
missing in its own process — so the rules cannot drift between the two.

Only adapters that need nothing but config are buildable here. Discord is not:
it needs the live discord.py client injected after the bot connects, so a
config-built one could not send anyway.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("navig.messaging.adapter_setup")

#: registry name → ``adapters.<section>`` config key
BUILDABLE: dict[str, str] = {"sms": "sms", "whatsapp": "whatsapp"}


def _section_enabled(section_cfg: object, default: bool = False) -> bool:
    from navig.core.coerce import coerce_bool

    if not isinstance(section_cfg, dict):
        return default
    return coerce_bool(section_cfg.get("enabled"), default=default)


def resolve_vault_placeholders(cfg: dict) -> dict:
    """Expand ``vault:KEY`` string values (one nesting level deep) from the vault.

    A placeholder whose secret is missing or unreadable is left as-is, so the
    adapter fails visibly on it rather than receiving an empty credential.
    """
    try:
        from navig.vault.core import get_vault

        vault = get_vault()
        if vault is None:
            return cfg
    except Exception:  # noqa: BLE001 — no vault: nothing to expand
        return cfg

    def _expand(d: dict) -> dict:
        out: dict = {}
        for k, v in d.items():
            if isinstance(v, str) and v.startswith("vault:"):
                try:
                    # get_secret returns a SecretStr — reveal before use.
                    sec = vault.get_secret(v[len("vault:"):])
                    raw = sec.reveal() if hasattr(sec, "reveal") else str(sec or "")
                    resolved = (raw or "").strip()
                    out[k] = resolved if resolved else v
                except Exception:  # noqa: BLE001
                    out[k] = v
            elif isinstance(v, dict):
                out[k] = _expand(v)
            else:
                out[k] = v
        return out

    return _expand(cfg)


def build(name: str, adapters_cfg: dict[str, Any]) -> tuple[Any | None, str]:
    """Construct the adapter *name* from ``adapters`` config.

    Returns ``(adapter, "")`` or ``(None, reason)``; never raises. ``reason``
    is fit to show a person ("SMS is off", "pip install twilio").
    """
    section = BUILDABLE.get(name)
    if section is None:
        return None, f"{name} cannot be built from config"
    cfg = adapters_cfg.get(section, {}) if isinstance(adapters_cfg, dict) else {}
    if not _section_enabled(cfg, False):
        return None, f"{name} is off (adapters.{section}.enabled)"
    try:
        resolved = resolve_vault_placeholders(cfg)
        if name == "sms":
            from navig.messaging.adapters.sms import SmsAdapter

            adapter = SmsAdapter(config=resolved)
            # The SDK import is lazy, so a missing package would otherwise only
            # surface as one error per send, forever. Refuse it here instead.
            missing = adapter.missing_dependency()
            if missing:
                return None, f"SMS needs the {missing!r} package: pip install {missing}"
            return adapter, ""
        if name == "whatsapp":
            from navig.messaging.adapters.whatsapp_cloud import WhatsAppCloudAdapter

            return WhatsAppCloudAdapter(config=resolved), ""
    except Exception as exc:  # noqa: BLE001 — a bad section must not break the caller
        logger.debug("adapter %s build failed", name, exc_info=True)
        return None, f"{name} could not be set up: {exc}"
    return None, f"{name} cannot be built from config"


def _adapters_config() -> dict[str, Any]:
    from navig.config import get_config_manager

    cfg = get_config_manager().get_global_config() or {}
    adapters = cfg.get("adapters", {})
    return adapters if isinstance(adapters, dict) else {}


def ensure_registered(name: str) -> tuple[bool, str]:
    """Make sure *name* is usable in THIS process. Returns ``(ok, reason)``.

    Already registered and enabled → ``(True, "")``. Otherwise, for a
    config-buildable adapter, build and register it now.
    """
    from navig.messaging.adapter_registry import get_adapter_registry

    reg = get_adapter_registry()
    if reg.is_available(name):
        return True, ""
    if name not in BUILDABLE:
        return False, f"{name} adapter not enabled"
    try:
        adapters_cfg = _adapters_config()
    except Exception as exc:  # noqa: BLE001
        return False, f"config unreadable: {exc}"
    adapter, reason = build(name, adapters_cfg)
    if adapter is None:
        return False, reason
    reg.register(adapter)
    if not reg.is_available(name):
        return False, f"{name} adapter registered but not enabled"
    logger.info("messaging adapter %s registered on demand in this process", name)
    return True, ""
