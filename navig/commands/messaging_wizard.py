"""
navig init --messaging — Interactive vault-first messaging adapter wizard.

Walks the user through configuring SMS (Twilio/Vonage), WhatsApp Cloud, and
Discord adapters. All credentials are stored in the NAVIG vault using the
canonical key names expected by ``_init_messaging_adapters()`` in
:mod:`navig.gateway.server`.

Design principles
-----------------
- Vault-first: credentials go to vault only — never to config files or env.
- Skip already-configured credentials without prompting.
- Exit cleanly at any step (Ctrl+C / empty input = skip adapter).
- Show the equivalent ``navig vault set`` command for each credential so
  advanced users can reproduce the setup manually.
- After storing credentials, auto-enable the adapter in global config and
  prompt the user to restart the service.
"""

from __future__ import annotations

import json
from typing import Any

from navig import console_helper as ch

# ── Adapter definitions ───────────────────────────────────────────────────────

_ADAPTERS: list[dict[str, Any]] = [
    {
        "id": "sms",
        "label": "SMS",
        "provider_label": "Twilio (recommended) or Vonage",
        "icon": "📱",
        "docs": "https://console.twilio.com — Account SID and Auth Token tab",
        "credentials": [
            {
                "label": "Account SID",
                "vault_key": "twilio_account_sid",
                "mask": False,
                "hint": "Starts with 'AC' — find it at console.twilio.com",
            },
            {
                "label": "Auth Token",
                "vault_key": "twilio_auth_token",
                "mask": True,
                "hint": "Found below the Account SID on the Twilio Console",
            },
            {
                "label": "From Phone Number",
                "vault_key": "twilio_phone_number",
                "mask": False,
                "hint": "E.164 format — e.g. +12025551234 (buy one at twilio.com/console/phone-numbers)",
            },
        ],
        "config_key": "sms",
    },
    {
        "id": "whatsapp",
        "label": "WhatsApp",
        "provider_label": "Meta WhatsApp Cloud API",
        "icon": "💚",
        "docs": "https://developers.facebook.com/docs/whatsapp/cloud-api",
        "credentials": [
            {
                "label": "Access Token",
                "vault_key": "whatsapp_cloud_token",
                "mask": True,
                "hint": "Meta Business App → System User access token (never expires) or temporary token",
            },
            {
                "label": "Phone Number ID",
                "vault_key": "whatsapp_phone_number_id",
                "mask": False,
                "hint": "Numeric ID from Meta Developer Console → WhatsApp → Phone Numbers",
            },
        ],
        "config_key": "whatsapp",
    },
    {
        "id": "discord",
        "label": "Discord",
        "provider_label": "Discord Bot",
        "icon": "🎮",
        "docs": "https://discord.com/developers/applications — Bot → Reset Token",
        "credentials": [
            {
                "label": "Bot Token",
                "vault_key": "discord_bot_token",
                "mask": True,
                "hint": "Discord Developer Portal → Your App → Bot → Reset Token",
            },
        ],
        "config_key": "discord",
    },
]


# ── Vault helpers ─────────────────────────────────────────────────────────────

def _get_vault():
    from navig.vault.core import get_vault  # noqa: PLC0415

    return get_vault()


def _vault_has(key: str) -> bool:
    try:
        v = _get_vault()
        if v is None:
            return False
        return bool((v.get_secret(key) or "").strip())
    except Exception:
        return False


def _vault_set(key: str, value: str) -> bool:
    try:
        v = _get_vault()
        if v is None:
            return False
        v.put(key, json.dumps({"value": value}).encode())
        return True
    except Exception:
        return False


# ── Config helper ─────────────────────────────────────────────────────────────

def _enable_adapter(adapter_id: str) -> bool:
    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        cfg = get_config_manager()
        adapters = dict(cfg.global_config.get("adapters", {}))
        adapter_cfg = dict(adapters.get(adapter_id, {}))
        adapter_cfg["enabled"] = True
        adapters[adapter_id] = adapter_cfg
        cfg.update_global_config({"adapters": adapters})
        return True
    except Exception:
        return False


# ── Wizard entry point ────────────────────────────────────────────────────────

def run_messaging_wizard() -> None:
    """Interactive vault-first messaging adapter setup wizard."""
    import typer

    ch.header("📡 Messaging Adapters Setup")
    ch.newline()
    ch.info("Configure SMS, WhatsApp, and Discord outbound messaging.")
    ch.info("All credentials are stored in the NAVIG vault — never in config files.")
    ch.dim('Press Enter with no value to skip any step.  Ctrl+C exits.')
    ch.newline()

    configured_any = False

    for adapter in _ADAPTERS:
        ada_id: str = adapter["id"]
        label: str = adapter["label"]
        icon: str = adapter["icon"]
        provider_label: str = adapter["provider_label"]
        docs: str = adapter["docs"]
        credentials: list[dict[str, Any]] = adapter["credentials"]

        # Check if all credentials already present
        all_present = all(_vault_has(c["vault_key"]) for c in credentials)
        if all_present:
            ch.success(f"{icon} {label} — already configured ✓")
            ch.newline()
            continue

        # ── Section header ────────────────────────────────────────────────
        ch.subheader(f"{icon} {label}  ({provider_label})")
        ch.dim(f"Guide: {docs}")
        ch.newline()

        try:
            proceed = typer.confirm(f"Set up {label} now?", default=False)
        except (KeyboardInterrupt, EOFError):
            ch.newline()
            ch.info("Setup cancelled.")
            break

        if not proceed:
            ch.dim(f"Skipped {label}.")
            ch.newline()
            continue

        # ── Collect credentials ───────────────────────────────────────────
        creds_to_save: dict[str, str] = {}
        aborted = False

        for cred in credentials:
            vault_key: str = cred["vault_key"]
            cred_label: str = cred["label"]
            mask: bool = cred["mask"]
            hint: str = cred["hint"]

            if _vault_has(vault_key):
                ch.success(f"  ✓ {cred_label} — already in vault")
                continue

            ch.info(f"  {cred_label}")
            ch.dim(f"    {hint}")
            ch.dim(f"    (manual: navig vault set {vault_key} <value>)")

            try:
                value = typer.prompt(
                    f"    {cred_label}",
                    default="",
                    show_default=False,
                    hide_input=mask,
                ).strip()
            except (KeyboardInterrupt, EOFError):
                ch.newline()
                aborted = True
                break

            if not value:
                ch.warning(f"  No value provided for '{cred_label}' — skipping {label}")
                aborted = True
                break

            creds_to_save[vault_key] = value

        if aborted:
            ch.newline()
            continue

        # ── Save to vault ─────────────────────────────────────────────────
        all_saved = True
        for key, value in creds_to_save.items():
            if _vault_set(key, value):
                ch.success(f"  ✓ {key} → vault")
            else:
                ch.error(f"  ✗ Failed to save {key} to vault")
                all_saved = False

        if not all_saved:
            ch.warning("Some credentials could not be saved. Check vault access.")
            ch.newline()
            continue

        if not creds_to_save:
            ch.dim(f"All {label} credentials already in vault.")
            ch.newline()
            continue

        # ── Enable in config ──────────────────────────────────────────────
        if _enable_adapter(ada_id):
            ch.success(f"✓ {label} adapter enabled in config")
        else:
            ch.warning(f"Could not auto-enable {label} in config.")
            ch.info(f"  Run: navig config set adapters.{ada_id}.enabled true")

        configured_any = True
        ch.newline()

    # ── Summary ───────────────────────────────────────────────────────────────
    ch.newline()
    if configured_any:
        ch.success("✓ Messaging adapter configuration complete.")
        ch.newline()
        ch.info("Activate changes:")
        ch.step("navig service restart")
        ch.newline()
        ch.info("Test a send (replace @alias and network as needed):")
        ch.step("navig dispatch send @alias sms 'Hello from NAVIG'")
        ch.newline()
        ch.info("Or use the Telegram bot:")
        ch.step("/sms @alias Your message")
    else:
        ch.info("No new adapters configured.")
        ch.dim("Run 'navig init --messaging' again when you're ready to set one up.")
    ch.newline()
