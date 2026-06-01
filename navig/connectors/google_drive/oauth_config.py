"""Google Drive OAuth configuration."""
from __future__ import annotations
import os
from navig.connectors.google_oauth_constants import GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL, GOOGLE_USERINFO_URL
from navig.providers.oauth import OAuthProviderConfig

GOOGLE_DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "openid", "email", "profile",
]

def build_google_drive_oauth_config(client_id: str, client_secret: str | None = None) -> OAuthProviderConfig:
    return OAuthProviderConfig(
        name="Google Drive",
        authorize_url=GOOGLE_AUTH_URL,
        token_url=GOOGLE_TOKEN_URL,
        client_id=client_id,
        client_secret=client_secret,
        scopes=GOOGLE_DRIVE_SCOPES,
        userinfo_url=GOOGLE_USERINFO_URL,
    )

def get_google_drive_oauth_config() -> OAuthProviderConfig | None:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    if not client_id:
        return None
    return build_google_drive_oauth_config(client_id, os.getenv("GOOGLE_CLIENT_SECRET"))
