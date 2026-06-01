"""Notion OAuth configuration."""
from __future__ import annotations
import os
from navig.providers.oauth import OAuthProviderConfig

def build_notion_oauth_config(client_id: str, client_secret: str | None = None) -> OAuthProviderConfig:
    return OAuthProviderConfig(
        name="Notion",
        authorize_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=[],  # Notion uses a single OAuth scope via the authorize flow
        userinfo_url="https://api.notion.com/v1/users/me",
    )

def get_notion_oauth_config() -> OAuthProviderConfig | None:
    client_id = os.getenv("NOTION_CLIENT_ID", "")
    if not client_id:
        return None
    return build_notion_oauth_config(client_id, os.getenv("NOTION_CLIENT_SECRET"))
