"""Connectors read the credentials the app saves — the vault, not only the environment.

YouTube, Google Maps, Perplexity, Supabase and GCP Translate documented "env variable or
navig vault" and read only ``os.environ``; the OAuth loaders for GitHub, Slack, Notion,
Linear and Drive did the same, and Calendar had no loader at all. Since the app can only
write to the vault, none of those could ever be connected from it.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from navig.connectors import secrets

_ENVS = (
    "YOUTUBE_API_KEY", "YT_API_KEY", "GOOGLE_MAPS_API_KEY", "GMAPS_API_KEY", "PERPLEXITY_API_KEY",
    "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY", "GCP_SERVICE_ACCOUNT_JSON",
    "GCP_SERVICE_ACCOUNT_FILE", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
    "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET",
    "NOTION_CLIENT_ID", "NOTION_CLIENT_SECRET", "LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET",
)


@pytest.fixture
def vault(monkeypatch):
    store: dict[str, str] = {}
    for name in _ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(secrets, "vault_secret", lambda label: store.get(label, ""))
    return store


@pytest.mark.parametrize(
    ("module", "cls_name", "labels", "attr"),
    [
        ("navig.connectors.youtube.connector", "YouTubeConnector", {"youtube/api_key": "yt"}, "_api_key"),
        ("navig.connectors.google_maps.connector", "GoogleMapsConnector", {"google_maps/api_key": "gm"}, "_api_key"),
        ("navig.connectors.perplexity.connector", "PerplexityConnector", {"perplexity/api_key": "pp"}, "_api_key"),
        (
            "navig.connectors.supabase.connector", "SupabaseConnector",
            {"supabase/url": "https://x.supabase.co/", "supabase/anon_key": "anon"}, "_anon_key",
        ),
    ],
)
def test_key_connector_connects_from_the_vault(vault, module, cls_name, labels, attr):
    import importlib

    vault.update(labels)
    inst = getattr(importlib.import_module(module), cls_name)()
    asyncio.run(inst.connect())
    assert getattr(inst, attr)


def test_supabase_url_from_the_vault_loses_its_trailing_slash(vault):
    from navig.connectors.supabase.connector import SupabaseConnector

    vault.update({"supabase/url": "https://x.supabase.co/", "supabase/anon_key": "anon"})
    inst = SupabaseConnector()
    asyncio.run(inst.connect())
    assert inst._url == "https://x.supabase.co"


def test_the_environment_still_wins(vault, monkeypatch):
    from navig.connectors.youtube.connector import YouTubeConnector

    vault["youtube/api_key"] = "from-vault"
    monkeypatch.setenv("YOUTUBE_API_KEY", "from-env")
    inst = YouTubeConnector()
    asyncio.run(inst.connect())
    assert inst._api_key == "from-env"


def test_gcp_service_account_loads_from_the_vault(vault):
    from navig.connectors.gcp_translate.connector import GcpTranslateConnector

    sa = {"type": "service_account", "client_email": "a@b", "private_key": "k", "project_id": "p"}
    vault["gcp/service_account_json"] = json.dumps(sa)
    assert GcpTranslateConnector()._load_service_account() == sa


@pytest.mark.parametrize(
    ("module", "fn", "ns"),
    [
        ("navig.connectors.github.oauth_config", "get_github_oauth_config", "github"),
        ("navig.connectors.slack.oauth_config", "get_slack_oauth_config", "slack"),
        ("navig.connectors.notion.oauth_config", "get_notion_oauth_config", "notion"),
        ("navig.connectors.linear.oauth_config", "get_linear_oauth_config", "linear"),
        ("navig.connectors.google_drive.oauth_config", "get_google_drive_oauth_config", "google"),
        ("navig.connectors.google_calendar.oauth_config", "get_calendar_oauth_config", "google"),
    ],
)
def test_oauth_loader_reads_the_app_credentials_from_the_vault(vault, module, fn, ns):
    import importlib

    loader = getattr(importlib.import_module(module), fn)
    assert loader() is None  # nothing configured → not registered
    vault[f"{ns}/oauth_client_id"] = "cid"
    vault[f"{ns}/oauth_client_secret"] = "csecret"
    config = loader()
    assert config is not None
    assert (config.client_id, config.client_secret) == ("cid", "csecret")


@pytest.mark.parametrize(
    ("module", "fn"),
    [
        ("navig.connectors.google_drive.oauth_config", "get_google_drive_oauth_config"),
        ("navig.connectors.google_calendar.oauth_config", "get_calendar_oauth_config"),
    ],
)
def test_google_links_ask_for_a_refresh_token(vault, module, fn):
    """Without offline access Google issues no refresh token and the link dies in an hour."""
    import importlib

    vault["google/oauth_client_id"] = "cid"
    config = getattr(importlib.import_module(module), fn)()
    assert config.extra_authorize_params == {"access_type": "offline", "prompt": "consent"}
    assert config.redirect_uri.endswith("/api/deck/connectors/oauth/callback")


def test_calendar_is_registered_by_the_deck_bootstrap(vault, monkeypatch):
    from navig.connectors import bootstrap
    from navig.connectors.auth_manager import ConnectorAuthManager

    registered = {}
    monkeypatch.setattr(ConnectorAuthManager, "register_provider", classmethod(lambda cls, cid, cfg: registered.setdefault(cid, cfg)))
    assert "google_calendar" in {cid for cid, _, _ in bootstrap.OAUTH_LOADERS}
    vault["google/oauth_client_id"] = "cid"
    assert bootstrap.register_oauth_providers(only="google_calendar") == ["google_calendar"]
    assert "google_calendar" in registered
