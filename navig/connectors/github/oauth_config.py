"""GitHub OAuth configuration."""
from __future__ import annotations

from navig.connectors.oauth_redirect import connector_redirect_uri
from navig.connectors.secrets import oauth_app_credentials
from navig.providers.oauth import OAuthProviderConfig

GITHUB_SCOPES = ["repo", "read:org", "read:user", "user:email"]

def build_github_oauth_config(client_id: str, client_secret: str | None = None) -> OAuthProviderConfig:
    return OAuthProviderConfig(
        name="GitHub",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=connector_redirect_uri(),
        scopes=GITHUB_SCOPES,
        userinfo_url="https://api.github.com/user",
    )

def get_github_oauth_config() -> OAuthProviderConfig | None:
    """Env ``GITHUB_CLIENT_ID``/``GITHUB_CLIENT_SECRET`` first, then the vault labels
    ``github/oauth_client_id`` / ``github/oauth_client_secret`` (what the app saves)."""
    client_id, client_secret = oauth_app_credentials("github", "GITHUB")
    if not client_id:
        return None
    return build_github_oauth_config(client_id, client_secret)
