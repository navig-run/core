"""
Google Calendar — OAuth Provider Configuration

Scopes and endpoint URLs for Google Calendar API v3.
"""

from __future__ import annotations

from navig.providers.oauth import OAuthProviderConfig

# Google OAuth 2.0 endpoints (shared)
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

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
        userinfo_url="https://www.googleapis.com/oauth2/v3/userinfo",
    )
