"""Where a connector's static credentials come from: the environment, then the vault.

Five connectors (YouTube, Google Maps, Perplexity, Supabase, GCP Translate) and every
connector's OAuth *app* credentials (client id / secret) are static values the operator
supplies once. Their docstrings promised "env variable **or navig vault**", but each one
read only ``os.environ`` — so a key saved from the app, which can only land in the vault,
changed nothing, and the app's Connect button for them could never work.

One resolution order for all of them: the first non-empty environment variable wins (power
users and tests can override without touching the vault), then the vault label. Values are
never logged.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("navig.connectors")


@dataclass(frozen=True)
class KeyField:
    """One value a connector needs before it can work — what the app asks the operator for.

    ``vault_label`` is where the app stores it; ``env`` names override it when set.
    """

    name: str
    label: str
    vault_label: str
    env: tuple[str, ...] = ()
    secret: bool = True
    multiline: bool = False
    optional: bool = False
    placeholder: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "secret": self.secret,
            "multiline": self.multiline,
            "optional": self.optional,
            "placeholder": self.placeholder,
        }


def vault_secret(label: str) -> str:
    """Best-effort vault read of *label*; '' when absent, locked or unreadable."""
    try:
        from navig.vault.core import get_vault

        value = get_vault().get_secret(label)
        value = value.reveal() if hasattr(value, "reveal") else str(value or "")
        return value.strip()
    except Exception:  # noqa: BLE001 — absent/locked/unreadable all mean "not configured"
        return ""


def resolve(field: KeyField) -> str:
    """The value for *field*: the first set environment variable, else the vault, else ''."""
    for name in field.env:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return vault_secret(field.vault_label)


def resolve_all(fields: list[KeyField] | tuple[KeyField, ...]) -> dict[str, str]:
    return {f.name: resolve(f) for f in fields}


def missing(fields: list[KeyField] | tuple[KeyField, ...]) -> list[str]:
    """Names of the required fields that resolve to nothing."""
    return [f.name for f in fields if not f.optional and not resolve(f)]


def store(field: KeyField, value: str) -> None:
    """Write *value* to the vault label *field* reads. Raises when the vault is unavailable."""
    from navig.vault.core import get_vault

    get_vault().put(field.vault_label, json.dumps({"value": value}).encode())


def forget(field: KeyField) -> bool:
    """Delete the vault label behind *field*; an environment override is untouched."""
    try:
        from navig.vault.core import get_vault

        return bool(get_vault().delete(field.vault_label))
    except Exception as exc:  # noqa: BLE001
        logger.debug("vault delete %s failed: %s", field.vault_label, exc)
        return False


def oauth_app_fields(namespace: str, env_prefix: str, label: str) -> tuple[KeyField, KeyField]:
    """The client id / secret pair of an OAuth app — ``<namespace>/oauth_client_id`` in the
    vault, ``<ENV_PREFIX>_CLIENT_ID`` in the environment (the names the loaders always read).
    """
    return (
        KeyField(
            name="client_id",
            label=f"{label} client ID",
            vault_label=f"{namespace}/oauth_client_id",
            env=(f"{env_prefix}_CLIENT_ID",),
            secret=False,
        ),
        KeyField(
            name="client_secret",
            label=f"{label} client secret",
            vault_label=f"{namespace}/oauth_client_secret",
            env=(f"{env_prefix}_CLIENT_SECRET",),
        ),
    )


def oauth_app_credentials(namespace: str, env_prefix: str) -> tuple[str, str | None]:
    """``(client_id, client_secret_or_None)`` for an OAuth app; ``('', None)`` when unset.

    Each value resolves on its own (environment, then vault). PKCE public clients have no
    secret, so a missing secret is ``None``, not "unconfigured".
    """
    cid_field, secret_field = oauth_app_fields(namespace, env_prefix, "")
    client_id = resolve(cid_field)
    if not client_id:
        return "", None
    return client_id, (resolve(secret_field) or None)
