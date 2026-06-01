"""Linear OAuth configuration."""
from __future__ import annotations
import os
from navig.providers.oauth import OAuthProviderConfig
from navig.connectors.oauth_redirect import connector_redirect_uri

LINEAR_SCOPES = ["read", "write", "issues:create"]

def build_linear_oauth_config(client_id: str, client_secret: str | None = None) -> OAuthProviderConfig:
    return OAuthProviderConfig(
        name="Linear",
        authorize_url="https://linear.app/oauth/authorize",
        token_url="https://api.linear.app/oauth/token",
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=connector_redirect_uri(),
        scopes=LINEAR_SCOPES,
        userinfo_url="https://api.linear.app/graphql",
    )

def get_linear_oauth_config() -> OAuthProviderConfig | None:
    client_id = os.getenv("LINEAR_CLIENT_ID", "")
    if not client_id:
        return None
    return build_linear_oauth_config(client_id, os.getenv("LINEAR_CLIENT_SECRET"))
