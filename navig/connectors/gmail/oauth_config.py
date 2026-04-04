"""
Gmail — OAuth Provider Configuration

Scopes and endpoint URLs for Google Gmail API v1.
The ``GMAIL_OAUTH_CONFIG`` is registered into the global
``OAUTH_PROVIDERS`` dict by :func:`register_gmail_oauth`.
"""

from __future__ import annotations

from navig.providers.oauth import OAuthProviderConfig

# Google OAuth 2.0 endpoints (shared across Google APIs)
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

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
        scopes=GMAIL_SCOPES,
        userinfo_url="https://www.googleapis.com/oauth2/v3/userinfo",
    )
