"""`fetch()` keeps accepting the domain keyword it was declared with before the rename.

The Supabase, YouTube and Google Maps connectors standardised on the base-class
``resource_id`` parameter. Keyword callers of the old API (``fetch(table=...)``,
``fetch(video_id=...)``, ``fetch(place_id=...)``) then raised ``TypeError`` for the
missing ``resource_id``; the legacy names are accepted as aliases.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.connectors.types import ConnectorStatus


def test_supabase_fetch_accepts_table_keyword(monkeypatch):
    from navig.connectors.supabase.connector import SupabaseConnector

    seen: dict[str, str] = {}

    def fake_request(url, headers, method="GET", body=None, timeout=15, **_k):
        seen["url"] = url
        return 200, [{"id": 1}]

    monkeypatch.setattr("navig.connectors.supabase.connector._sb_request", fake_request)
    c = SupabaseConnector()
    c._url = "https://proj.supabase.co"
    c._anon_key = "anon-key"
    c._status = ConnectorStatus.CONNECTED
    res = asyncio.run(c.fetch(table="users"))
    assert res is not None and res.id == "users"
    assert "/rest/v1/users?" in seen["url"]


def test_youtube_fetch_accepts_video_id_keyword(monkeypatch):
    from navig.connectors.youtube import connector as yt

    seen: dict[str, str] = {}

    def fake_get(endpoint, params, timeout=12):
        seen.update(params)
        return {"items": []}

    monkeypatch.setattr(yt, "_yt_get", fake_get)
    c = yt.YouTubeConnector()
    c._api_key = "k"
    assert asyncio.run(c.fetch(video_id="abc123")) is None
    assert seen["id"] == "abc123"


def test_google_maps_fetch_accepts_place_id_keyword(monkeypatch):
    from navig.connectors.google_maps import connector as gm

    seen: dict[str, str] = {}

    def fake_get(url, params, timeout=12):
        seen.update(params)
        return {"status": "NOT_FOUND"}

    monkeypatch.setattr(gm, "_get", fake_get)
    c = gm.GoogleMapsConnector()
    c._api_key = "k"
    assert asyncio.run(c.fetch(place_id="ChIJxyz")) is None
    assert seen["place_id"] == "ChIJxyz"


def test_fetch_without_any_id_still_raises_type_error():
    from navig.connectors.youtube.connector import YouTubeConnector

    c = YouTubeConnector()
    c._api_key = "k"
    with pytest.raises(TypeError, match="resource_id"):
        asyncio.run(c.fetch())
