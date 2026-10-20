"""GET /api/deck/services and its credentials routes — one list, a working action per row.

The app's "Connect your services" listed the 12 brain connectors only, and six could not
connect: the five API-key connectors answered 404 (they have no OAuth flow) and Google
Calendar had no OAuth loader in the deck path. A connector whose OAuth app was unset also
404'd without saying why. These tests pin the replacement: every row says what it needs,
and saving it is what makes Connect work.

The vault is replaced by a dict (``fake_vault``) — nothing here touches a real vault.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("aiohttp")

from navig.connectors import secrets  # noqa: E402
from navig.connectors.auth_manager import ConnectorAuthManager  # noqa: E402
from navig.gateway.deck import service_sources  # noqa: E402
from navig.gateway.deck.routes import services, social  # noqa: E402

_KEY_ENVS = (
    "YOUTUBE_API_KEY", "YT_API_KEY", "GOOGLE_MAPS_API_KEY", "GMAPS_API_KEY", "PERPLEXITY_API_KEY",
    "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY", "GCP_SERVICE_ACCOUNT_JSON",
    "GCP_SERVICE_ACCOUNT_FILE", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
)


class _FakeReq:
    def __init__(self, body=None, match_info=None):
        self._body = body
        self.match_info = match_info or {}

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture
def fake_vault(monkeypatch):
    store: dict[str, str] = {}
    for name in _KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(secrets, "vault_secret", lambda label: store.get(label, ""))
    monkeypatch.setattr(secrets, "store", lambda field, value: store.__setitem__(field.vault_label, value))
    monkeypatch.setattr(
        secrets, "forget", lambda field: store.pop(field.vault_label, None) is not None
    )
    monkeypatch.setattr(ConnectorAuthManager, "list_connected_accounts", lambda self: {})
    monkeypatch.setattr(social, "social_status_snapshot", lambda: {"telegram": {"connected": True}})
    return store


@pytest.fixture
def providers(monkeypatch):
    """The set of connector ids whose OAuth provider config is registered."""
    ready: set[str] = set()
    monkeypatch.setattr(
        ConnectorAuthManager, "get_provider_config", classmethod(lambda cls, cid: object() if cid in ready else None)
    )
    return ready


def _rows():
    rows, sources = services.build_services()
    return {r["id"]: r for r in rows}, sources


def test_every_row_has_a_category_and_an_action(fake_vault, providers):
    rows, sources = _rows()
    assert sources["connector"] == "ok" and sources["messaging"] == "ok"
    assert len([r for r in rows if r.startswith("connector:")]) >= 12
    for row in rows.values():
        assert row["category"] in services.CATEGORIES, row["id"]
        assert row["connect"]["kind"] in {"oauth", "api_key", "settings", "external"}, row["id"]
        assert row["status"] in {"connected", "available", "needs_setup"}, row["id"]


@pytest.mark.parametrize("cid", ["youtube", "google_maps", "perplexity", "supabase", "gcp_translate"])
def test_key_connectors_offer_fields_not_a_dead_oauth_button(fake_vault, providers, cid):
    row = _rows()[0][f"connector:{cid}"]
    assert row["connect"]["kind"] == "api_key"
    assert row["connect"]["fields"], "a key connector must say what it needs"
    assert row["connect"]["save"] == f"/api/deck/services/connector:{cid}/credentials"
    assert row["status"] == "available"


def test_a_key_saved_in_the_vault_counts_as_connected(fake_vault, providers):
    fake_vault["youtube/api_key"] = "AIza-test"
    row = _rows()[0]["connector:youtube"]
    assert row["status"] == "connected"
    assert row["accounts"] and row["accounts"][0]["status"] == "ok"


def test_oauth_connector_without_its_app_asks_for_client_id_and_says_why(fake_vault, providers):
    row = _rows()[0]["connector:google_calendar"]
    assert row["status"] == "needs_setup"
    assert row["connect"]["ready"] is False
    assert [f["name"] for f in row["connect"]["fields"]] == ["client_id", "client_secret"]
    assert "client ID" in row["connect"]["reason"]
    assert row["connect"]["redirect_uri"].endswith("/api/deck/connectors/oauth/callback")


def test_oauth_connector_with_its_app_is_ready_to_connect(fake_vault, providers):
    providers.add("google_calendar")
    row = _rows()[0]["connector:google_calendar"]
    assert row["status"] == "available"
    assert row["connect"]["ready"] is True
    assert row["connect"]["start"] == "/api/deck/connectors/google_calendar/connect"
    assert "fields" not in row["connect"]


def test_messaging_rows_point_at_their_settings(fake_vault, providers):
    rows = _rows()[0]
    assert rows["messaging:telegram"]["status"] == "connected"
    assert rows["messaging:telegram"]["connect"] == {"kind": "settings", "ready": True, "settings": "telegram"}
    assert rows["messaging:discord"]["status"] == "available"


def test_a_failing_plugin_source_is_reported_not_fatal(fake_vault, providers, monkeypatch):
    def boom():
        raise RuntimeError("plugin broke")

    good = [{"id": "social:youtube", "name": "YouTube channel", "category": "social",
             "status": "connected", "connect": {"kind": "oauth", "ready": True}}]
    monkeypatch.setattr(service_sources, "_SOURCES", {"broken": boom, "social": lambda: good})
    rows, sources = _rows()
    assert sources["broken"] == "plugin broke"
    assert sources["social"] == "ok"
    # Both YouTubes list: the Data API connector and the channel publisher are different things.
    assert "social:youtube" in rows and "connector:youtube" in rows


# ── credentials ──────────────────────────────────────────────────────────────


def _save(service_id, values):
    req = _FakeReq({"values": values}, {"service_id": service_id})
    return asyncio.run(services.handle_deck_services_credentials_save(req))


def test_saving_a_working_key_stores_it(fake_vault, providers, monkeypatch):
    async def ok(cls):
        return True, "ok"

    monkeypatch.setattr(services, "_probe", ok)
    resp = _save("connector:youtube", {"api_key": "  AIza-good  "})
    assert resp.status == 200
    assert fake_vault["youtube/api_key"] == "AIza-good"


def test_a_rejected_key_is_rolled_back_and_the_reason_returned(fake_vault, providers, monkeypatch):
    fake_vault["youtube/api_key"] = "AIza-old"

    async def bad(cls):
        return False, "API key not valid"

    monkeypatch.setattr(services, "_probe", bad)
    resp = _save("connector:youtube", {"api_key": "AIza-wrong"})
    assert resp.status == 422
    assert fake_vault["youtube/api_key"] == "AIza-old"


def test_a_rejected_first_key_leaves_nothing_behind(fake_vault, providers, monkeypatch):
    async def bad(cls):
        return False, "nope"

    monkeypatch.setattr(services, "_probe", bad)
    assert _save("connector:perplexity", {"api_key": "pplx-wrong"}).status == 422
    assert "perplexity/api_key" not in fake_vault


def test_oauth_app_credentials_register_the_provider_at_once(fake_vault, providers, monkeypatch):
    # The connector registry bootstraps lazily on first use and calls
    # register_oauth_providers() itself. Bootstrap it BEFORE patching, or a run where this
    # test is the first to touch the registry (e.g. run alone) counts that call too.
    from navig.connectors.bootstrap import ensure_connectors_loaded

    ensure_connectors_loaded()
    calls = []
    monkeypatch.setattr(
        "navig.connectors.bootstrap.register_oauth_providers", lambda only=None: calls.append(only) or []
    )
    resp = _save("connector:google_calendar", {"client_id": "id.apps.googleusercontent.com", "client_secret": "s"})
    assert resp.status == 200
    assert fake_vault["google/oauth_client_id"] == "id.apps.googleusercontent.com"
    assert fake_vault["google/oauth_client_secret"] == "s"
    assert calls == [None]


@pytest.mark.parametrize(
    ("service_id", "values", "status"),
    [
        ("connector:youtube", {"nope": "x"}, 400),
        ("connector:youtube", {"api_key": 5}, 400),
        ("connector:youtube", {"api_key": ""}, 400),
        ("connector:supabase", {"anon_key": "k"}, 400),  # the URL is required too
        ("connector:google_calendar", {"client_secret": "s"}, 400),
        ("connector:nope", {"api_key": "x"}, 404),
        ("social:youtube", {"api_key": "x"}, 404),
        ("connector:youtube", {}, 400),
    ],
)
def test_bad_credential_bodies_are_rejected_before_anything_is_stored(fake_vault, providers, service_id, values, status):
    assert _save(service_id, values).status == status
    assert fake_vault == {}


def test_forgetting_a_key_disconnects(fake_vault, providers):
    fake_vault["google_maps/api_key"] = "AIza"
    req = _FakeReq(None, {"service_id": "connector:google_maps"})
    resp = asyncio.run(services.handle_deck_services_credentials_delete(req))
    assert resp.status == 200
    assert "google_maps/api_key" not in fake_vault


def test_every_linked_account_is_listed_with_its_own_remove(fake_vault, providers, monkeypatch):
    providers.add("gmail")
    monkeypatch.setattr(ConnectorAuthManager, "list_connected_accounts", lambda self: {"gmail": "work@example.com"})
    monkeypatch.setattr(ConnectorAuthManager, "list_accounts", lambda self, cid: ["work@example.com", "me+news@example.com"])
    row = _rows()[0]["connector:gmail"]
    assert row["status"] == "connected" and row["multi_account"] is True
    assert [(a["label"], a["remove"]) for a in row["accounts"]] == [
        ("work@example.com", "/api/deck/connectors/gmail?account=work%40example.com"),
        ("me+news@example.com", "/api/deck/connectors/gmail?account=me%2Bnews%40example.com"),
    ]
