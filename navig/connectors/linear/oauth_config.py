"""Linear OAuth configuration."""
from __future__ import annotations

from navig.connectors.oauth_redirect import connector_redirect_uri
from navig.connectors.secrets import oauth_app_credentials
from navig.providers.oauth import OAuthProviderConfig

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
    """Env ``LINEAR_CLIENT_ID``/``LINEAR_CLIENT_SECRET`` first, then the vault labels
    ``linear/oauth_client_id`` / ``linear/oauth_client_secret`` (what the app saves)."""
    client_id, client_secret = oauth_app_credentials("linear", "LINEAR")
    if not client_id:
        return None
    return build_linear_oauth_config(client_id, client_secret)
