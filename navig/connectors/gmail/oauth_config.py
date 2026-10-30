"""
Gmail — OAuth Provider Configuration

Scopes and endpoint URLs for Google Gmail API v1.

Registration path (neither of the two names this docstring used to give exists —
there is no ``GMAIL_OAUTH_CONFIG`` constant and no ``register_gmail_oauth``):
:func:`get_gmail_oauth_config` is called by ``connectors.bootstrap``, which walks
its ``OAUTH_LOADERS`` table and hands the result to
``ConnectorAuthManager.register_provider("gmail", config)`` — that is what
populates the global ``OAUTH_PROVIDERS`` dict. Returning ``None`` (no
``GOOGLE_CLIENT_ID`` in the environment) skips registration silently, which is
why an unconfigured install simply has no ``gmail`` provider rather than an error.
"""

from __future__ import annotations

import os

from navig._daemon_defaults import _OAUTH_REDIRECT_PORT
from navig.connectors.google_oauth_constants import (
    GOOGLE_AUTH_URL as _GOOGLE_AUTH_URL,
)
from navig.connectors.google_oauth_constants import (
    GOOGLE_TOKEN_URL as _GOOGLE_TOKEN_URL,
)
from navig.connectors.google_oauth_constants import (
    GOOGLE_USERINFO_URL as _GOOGLE_USERINFO_URL,
)
from navig.connectors.oauth_redirect import connector_redirect_uri
from navig.providers.oauth import OAuthProviderConfig

# Vault labels the operator writes with ``navig vault set google/oauth_client_id …``
# (the `set` verb also stores the exact slash path as a label, which is what
# ``get_secret`` resolves). Env vars still win when present.
VAULT_CLIENT_ID_LABEL = "google/oauth_client_id"
VAULT_CLIENT_SECRET_LABEL = "google/oauth_client_secret"

# Gmail-specific scopes
# https://developers.google.com/gmail/api/auth/scopes
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
    "openid",
    "email",
    "profile",
]


#: Opt-in only: reading and creating Gmail FILTERS (``settings/filters``) needs this scope.
#: Requested when ``connectors.gmail.filters`` is true, so nobody is asked to grant filter
#: management unless they turned on the mailroom's filter organisation. Takes effect at the
#: next connect (``navig connector connect gmail``); existing tokens keep their old scopes.
GMAIL_SETTINGS_SCOPE = "https://www.googleapis.com/auth/gmail.settings.basic"


def gmail_scopes() -> list[str]:
    """The scopes a new Gmail connection asks for."""
    scopes = list(GMAIL_SCOPES)
    try:
        from navig.config import get_config_manager
        from navig.core.coerce import coerce_bool

        cfg = get_config_manager().get_global_config() or {}
        wanted = ((cfg.get("connectors") or {}).get("gmail") or {}).get("filters")
        if coerce_bool(wanted, default=False):
            scopes.append(GMAIL_SETTINGS_SCOPE)
    except Exception:  # noqa: BLE001 — an unreadable config asks for the default scopes, never more
        pass
    return scopes


def build_gmail_oauth_config(
    client_id: str,
    client_secret: str | None = None,
) -> OAuthProviderConfig:
    """
    Build a Gmail ``OAuthProviderConfig``.

    ``client_id`` and ``client_secret`` come from the Google Cloud Console
    OAuth 2.0 credentials page.  They are injected at runtime via
    environment variables or the NAVIG vault — never hard-coded.
    """
    return OAuthProviderConfig(
        name="Gmail",
        authorize_url=_GOOGLE_AUTH_URL,
        token_url=_GOOGLE_TOKEN_URL,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=connector_redirect_uri(),
        scopes=gmail_scopes(),
        userinfo_url=_GOOGLE_USERINFO_URL,
        # Without these Google returns NO refresh token: the connection then dies
        # one hour after linking, which is fatal for unattended cron/daemon use.
        # ``prompt=consent`` forces the refresh token on re-links too (Google only
        # issues it on the first consent otherwise).
        extra_authorize_params={"access_type": "offline", "prompt": "consent"},
        # Google "Desktop app" clients accept any loopback redirect, so the CLI
        # can complete on its own listener instead of the gateway callback.
        cli_redirect_uri=f"http://127.0.0.1:{_OAUTH_REDIRECT_PORT}/auth/callback",
    )


def _vault_secret(label: str) -> str:
    """Best-effort vault read of *label*; '' when absent/unreadable. Never logs the value."""
    try:
        from navig.vault.core import get_vault

        value = get_vault().get_secret(label)
        value = value.reveal() if hasattr(value, "reveal") else str(value or "")
        return value.strip()
    except Exception:  # noqa: BLE001 — vault missing/locked/label absent all mean "not configured"
        return ""


def get_gmail_oauth_config() -> OAuthProviderConfig | None:
    """Load Gmail OAuth config: env ``GOOGLE_CLIENT_ID``/``GOOGLE_CLIENT_SECRET`` first,
    then the vault labels ``google/oauth_client_id`` / ``google/oauth_client_secret``.
    Returns None when unconfigured (registration is then silently skipped)."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret: str | None = os.getenv("GOOGLE_CLIENT_SECRET") or None
    if not client_id:
        client_id = _vault_secret(VAULT_CLIENT_ID_LABEL)
        if not client_id:
            return None
    # Each credential falls back on its own: an env client id with the secret kept
    # only in the vault is a valid split, and must not drop the secret.
    client_secret = client_secret or _vault_secret(VAULT_CLIENT_SECRET_LABEL) or None
    return build_gmail_oauth_config(client_id, client_secret)
