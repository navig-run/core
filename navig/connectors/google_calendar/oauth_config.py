"""
Google Calendar — OAuth Provider Configuration

Scopes and endpoint URLs for Google Calendar API v3.
"""

from __future__ import annotations

from navig.connectors.google_oauth_constants import (
    GOOGLE_AUTH_URL as _GOOGLE_AUTH_URL,
)
from navig.connectors.google_oauth_constants import (
    GOOGLE_TOKEN_URL as _GOOGLE_TOKEN_URL,
)
from navig.connectors.google_oauth_constants import (
    GOOGLE_USERINFO_URL as _GOOGLE_USERINFO_URL,
)
from navig.providers.oauth import OAuthProviderConfig

# Calendar-specific scopes
# https://developers.google.com/calendar/api/auth
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "email",
    "profile",
]


def build_calendar_oauth_config(
    client_id: str,
    client_secret: str | None = None,
) -> OAuthProviderConfig:
    """
    Build a Google Calendar ``OAuthProviderConfig``.

    Credentials come from Google Cloud Console, injected via env vars
    or vault at runtime.
    """
    return OAuthProviderConfig(
        name="Google Calendar",
        authorize_url=_GOOGLE_AUTH_URL,
        token_url=_GOOGLE_TOKEN_URL,
        client_id=client_id,
        client_secret=client_secret,
        scopes=CALENDAR_SCOPES,
        userinfo_url=_GOOGLE_USERINFO_URL,
    )
