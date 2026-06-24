"""Tests for the Telegram network-manager catalog (Part 1).

Covers the store, the ingestion hook (incl. channel_post which the assistant
flow ignores), the media analyzer orchestration, and the deck route handlers.
Isolated via the session ``NAVIG_CONFIG_DIR`` fixture plus a per-test store
pointed at ``tmp_path``.
"""

from __future__ import annotations

import json

import pytest

from navig.store import telegram_catalog as tc
from navig.store.telegram_catalog import TelegramCatalogStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Fresh catalog store wired in as the module singleton."""
    s = TelegramCatalogStore(db_path=tmp_path / "catalog.db")
    monkeypatch.setattr(tc, "_store", s)
    return s


class FakeChannel:
    bot_token = "TESTTOKEN"
    _session = None

    def __init__(self):
        self.calls: list[tuple] = []

    async def _api_call(self, method, data=None):
        self.calls.append((method, data))
        if method == "getChat":
            return {"id": data["chat_id"], "type": "channel", "title": "Synced Title", "username": "sync"}
        if method == "getChatMemberCount":
            return 1234
        if method == "getMe":
            return {"id": 999}
        if method == "getChatMember":
            return {"status": "administrator", "can_delete_messages": True, "can_post_messages": True}
        return None

    async def send_message(self, chat_id, text, parse_mode="HTML", reply_to_message_id=None, keyboard=None):
        self.calls.append(("send", chat_id, text))
        return {"message_id": 555}

    async def edit_message(self, chat_id, message_id, text, parse_mode="HTML", keyboard=None):
        self.calls.append(("edit", chat_id, message_id, text))
        return {"message_id": message_id}

    async def delete_message(self, chat_id, message_id):
        self.calls.append(("delete", chat_id, message_id))
        return True


# ── Store ─────────────────────────────────────────────────────


def test_store_roundtrip_and_counts(store):
    store.upsert_room(-100, type="channel", title="Chan", username="c", bot_is_admin=True, can_delete=True)
    mid = store.upsert_media(-100, message_id=2, file_id="f", file_unique_id="u1", kind="photo", size=10)
    store.upsert_message(-100, 1, text="hello world", kind="text", date="2026-06-19T10:00:00Z")
    store.upsert_message(-100, 2, text="a caption", media_ref=mid, kind="photo", date="2026-06-19T10:01:00Z")

    rooms = store.list_rooms()
    assert len(rooms) == 1
    assert rooms[0]["message_count"] == 2
    assert rooms[0]["media_count"] == 1
    assert rooms[0]["bot_is_admin"] is True

    msgs = store.list_messages(-100)
    assert {m["message_id"] for m in msgs} == {1, 2}
    photo_msg = next(m for m in msgs if m["message_id"] == 2)
    assert photo_msg["media"]["kind"] == "photo"

    assert len(store.list_messages(-100, kind="photo")) == 1
    assert len(store.list_media(-100, kind="photo")) == 1


def test_store_media_dedup_by_unique_id(store):
    a = store.upsert_media(-100, message_id=1, file_id="f1", file_unique_id="same", kind="photo")
    b = store.upsert_media(-100, message_id=1, file_id="f2", file_unique_id="same", kind="photo")
    assert a == b  # deduped


def test_store_search_messages_and_media(store):
    store.upsert_room(-100, type="channel", title="Chan")
    store.upsert_message(-100, 1, text="quarterly invoice numbers", kind="text")
    mid = store.upsert_media(-100, message_id=2, file_id="f", file_unique_id="u", kind="photo")
    store.set_analysis(mid, ocr_text="NEON SIGN", ai_description="a glowing neon sign")

    hits = store.search("invoice")
    assert any(h["ref_kind"] == "message" for h in hits)
    hits2 = store.search("neon")
    assert any(h["ref_kind"] == "media" for h in hits2)
    # Scoped search
    assert store.search("invoice", chat_id=-999) == []


def test_store_edit_and_delete(store):
    store.upsert_room(-100, type="group", title="G")
    local = store.upsert_message(-100, 7, text="original", kind="text")
    store.update_message_text(local, "edited body")
    assert store.get_message(local)["text"] == "edited body"
    assert store.search("edited")  # FTS reflects the edit

    assert store.mark_message_deleted(-100, 7) is True
    assert store.list_messages(-100) == []  # deleted excluded by default
    assert len(store.list_messages(-100, include_deleted=True)) == 1


def test_pending_media(store):
    m1 = store.upsert_media(-100, file_unique_id="a", kind="photo")
    store.upsert_media(-100, file_unique_id="b", kind="video")
    store.set_analysis(m1, status="done", ocr_text="x")
    pending = store.pending_media()
    assert len(pending) == 1
    assert pending[0]["kind"] == "video"


# ── Ingestion ─────────────────────────────────────────────────


@pytest.fixture
def enable_catalog(monkeypatch):
    from navig.gateway.channels import telegram_catalog_ingest as ing

    monkeypatch.setattr(ing, "catalog_config", lambda: {"enabled": True, "auto_analyze": False})
    monkeypatch.setattr(ing, "catalog_enabled", lambda: True)
    return ing


async def test_ingest_channel_post_with_media(store, enable_catalog):
    ing = enable_catalog
    update = {
        "channel_post": {
            "message_id": 10,
            "date": 1718790000,
            "chat": {"id": -1001, "type": "channel", "title": "My Channel", "username": "mychan"},
            "caption": "rocket launch photo",
            "photo": [
                {"file_id": "s", "file_unique_id": "us", "file_size": 100},
                {"file_id": "l", "file_unique_id": "ul", "file_size": 9000},
            ],
        }
    }
    await ing.ingest_update(FakeChannel(), update)

    rooms = store.list_rooms()
    assert rooms[0]["chat_id"] == -1001
    media = store.list_media(-1001)
    assert media[0]["file_id"] == "l"  # largest variant chosen
    assert store.search("rocket")


def test_catalog_auto_enable_and_coercion(monkeypatch):
    from navig.gateway.channels import telegram_catalog_ingest as ing

    # No explicit flag → auto-on when a bot token is configured.
    monkeypatch.setattr(ing, "catalog_config", lambda: {})
    monkeypatch.setattr(ing, "_telegram_token_present", lambda: True)
    assert ing.catalog_enabled() is True
    monkeypatch.setattr(ing, "_telegram_token_present", lambda: False)
    assert ing.catalog_enabled() is False

    # Explicit flag wins — including the string "false" that `config set` stores.
    monkeypatch.setattr(ing, "catalog_config", lambda: {"enabled": "false"})
    monkeypatch.setattr(ing, "_telegram_token_present", lambda: True)
    assert ing.catalog_enabled() is False
    monkeypatch.setattr(ing, "catalog_config", lambda: {"enabled": "true"})
    assert ing.catalog_enabled() is True
    monkeypatch.setattr(ing, "catalog_config", lambda: {"enabled": True})
    assert ing.catalog_enabled() is True


async def test_ingest_disabled_is_noop(store, monkeypatch):
    from navig.gateway.channels import telegram_catalog_ingest as ing

    monkeypatch.setattr(ing, "catalog_enabled", lambda: False)
    await ing.ingest_update(FakeChannel(), {"message": {"message_id": 1, "chat": {"id": 1}, "text": "x"}})
    assert store.list_rooms() == []


async def test_ingest_edited_message_updates(store, enable_catalog):
    ing = enable_catalog
    base = {"message": {"message_id": 5, "date": 1718790000, "chat": {"id": -1, "type": "group"}, "text": "v1"}}
    await ing.ingest_update(FakeChannel(), base)
    edit = {"edited_message": {"message_id": 5, "date": 1718790000, "edit_date": 1718790500,
                              "chat": {"id": -1, "type": "group"}, "text": "v2 edited"}}
    await ing.ingest_update(FakeChannel(), edit)
    msgs = store.list_messages(-1)
    assert len(msgs) == 1
    assert msgs[0]["text"] == "v2 edited"
    assert msgs[0]["edited_at"]


async def test_sync_room_meta(store, enable_catalog):
    ing = enable_catalog
    ch = FakeChannel()
    room = await ing.sync_room_meta(ch, -1001)
    assert room["title"] == "Synced Title"
    assert room["member_count"] == 1234
    assert room["bot_is_admin"] is True
    assert room["can_delete"] is True


# ── Analyzer ──────────────────────────────────────────────────


async def test_analyze_image_orchestration(store, monkeypatch):
    from navig.gateway.channels import telegram_catalog_analyzer as az

    mid = store.upsert_media(-100, message_id=1, file_id="f", file_unique_id="u", kind="photo", size=1000)

    async def fake_download(channel, media):
        return b"imgbytes"

    async def fake_image(data):
        return ("OCR TEXT", "an AI description")

    monkeypatch.setattr(az, "_download", fake_download)
    monkeypatch.setattr(az, "_analyze_image", fake_image)

    result = await az.analyze_media(FakeChannel(), mid)
    assert result["ok"] and result["status"] == "done"
    media = store.get_media(mid)
    assert media["ocr_text"] == "OCR TEXT"
    assert media["ai_description"] == "an AI description"
    assert media["analysis_status"] == "done"


async def test_analyze_too_large_skipped(store):
    from navig.gateway.channels import telegram_catalog_analyzer as az

    mid = store.upsert_media(-100, file_unique_id="u", kind="photo", size=999_000_000)
    result = await az.analyze_media(FakeChannel(), mid)
    assert result["error"] == "too_large"
    assert store.get_media(mid)["analysis_status"] == "skipped"


async def test_analyze_download_failure(store, monkeypatch):
    from navig.gateway.channels import telegram_catalog_analyzer as az

    mid = store.upsert_media(-100, file_unique_id="u", kind="voice", size=10)
    monkeypatch.setattr(az, "_download", lambda c, m: _none())
    result = await az.analyze_media(FakeChannel(), mid)
    assert result["error"] == "download_failed"
    assert store.get_media(mid)["analysis_status"] == "error"


async def _none():
    return None


# ── Routes ────────────────────────────────────────────────────


class FakeRequest:
    def __init__(self, match=None, query=None, body=None):
        self.match_info = match or {}
        self.query = query or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _payload(resp):
    return json.loads(resp.body)


async def test_route_rooms_and_search(store):
    from navig.gateway.deck.routes import telegram_manager as tm

    store.upsert_room(-100, type="channel", title="Chan")
    store.upsert_message(-100, 1, text="searchable text here", kind="text")

    resp = await tm.handle_rooms_list(FakeRequest())
    body = _payload(resp)
    assert body["ok"] and len(body["data"]["rooms"]) == 1

    resp = await tm.handle_search(FakeRequest(query={"q": "searchable"}))
    assert len(_payload(resp)["data"]["results"]) == 1


async def test_route_edit_requires_confirm(store, monkeypatch):
    from navig.gateway.deck.routes import telegram_manager as tm

    store.upsert_room(-100, type="group", title="G")
    local = store.upsert_message(-100, 7, text="orig", kind="text")
    monkeypatch.setattr(tm, "_channel", lambda request=None: FakeChannel())

    # Missing confirm → 412
    resp = await tm.handle_message_edit(FakeRequest(match={"id": str(local)}, body={"text": "new"}))
    assert resp.status == 412

    # With confirm → edits + persists
    resp = await tm.handle_message_edit(
        FakeRequest(match={"id": str(local)}, body={"text": "new body", "confirm": True})
    )
    assert resp.status == 200
    assert store.get_message(local)["text"] == "new body"


async def test_route_delete_requires_confirm_and_channel(store, monkeypatch):
    from navig.gateway.deck.routes import telegram_manager as tm

    store.upsert_room(-100, type="group", title="G")
    local = store.upsert_message(-100, 8, text="bye", kind="text")

    # No running bot → 503
    monkeypatch.setattr(tm, "_channel", lambda request=None: None)
    resp = await tm.handle_message_delete(FakeRequest(match={"id": str(local)}, body={"confirm": True}))
    assert resp.status == 503

    # Channel present + confirm → deletes
    monkeypatch.setattr(tm, "_channel", lambda request=None: FakeChannel())
    resp = await tm.handle_message_delete(FakeRequest(match={"id": str(local)}, body={"confirm": True}))
    assert resp.status == 200
    assert store.list_messages(-100) == []


async def test_route_post(store, monkeypatch):
    from navig.gateway.deck.routes import telegram_manager as tm

    monkeypatch.setattr(tm, "_channel", lambda request=None: FakeChannel())
    resp = await tm.handle_room_post(FakeRequest(match={"id": "-100"}, body={"text": "hello room"}))
    body = _payload(resp)
    assert body["ok"] and body["data"]["message_id"] == 555
