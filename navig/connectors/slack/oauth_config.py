"""Slack OAuth configuration."""
from __future__ import annotations
import os
from navig.providers.oauth import OAuthProviderConfig

SLACK_SCOPES = ["channels:read", "channels:history", "chat:write", "users:read", "search:read"]

def build_slack_oauth_config(client_id: str, client_secret: str | None = None) -> OAuthProviderConfig:
    return OAuthProviderConfig(
        name="Slack",
        authorize_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SLACK_SCOPES,
        userinfo_url="https://slack.com/api/auth.test",
    )

def get_slack_oauth_config() -> OAuthProviderConfig | None:
    client_id = os.getenv("SLACK_CLIENT_ID", "")
    if not client_id:
        return None
    return build_slack_oauth_config(client_id, os.getenv("SLACK_CLIENT_SECRET"))
