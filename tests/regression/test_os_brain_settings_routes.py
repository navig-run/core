"""Deck routes NAVIG OS Settings → NAVIG notifications / Brain depend on.

Four defects found while porting the Mini App's settings to the desktop:

* "Send test" to a channel went through ``dispatch("custom", only_channels=[ch])``.
  ``custom`` routes to the deck only by default, so a Telegram test resolved to zero
  channels and reported failure for a working Telegram.
* ``/audio`` and ``/persona`` keyed per-user state on ``deck_user_id``. A desktop /
  Bearer request carries a sentinel there, so NAVIG OS changed the voice and persona
  of a user that does not exist while the bot (which reads by Telegram id) kept the
  old ones.
* The Matrix settings routes read and wrote a top-level ``matrix.*`` section; the
  Matrix bot starts from ``comms.matrix.*``. The deck's Matrix switch changed nothing.
* ``enabled`` in POST bodies was read with ``bool()`` — the string ``"false"`` is True.
"""

from __future__ import annotations

import json

import pytest


class _Req(dict):
    """Just enough of ``aiohttp.web.Request`` for these handlers."""

    def __init__(self, body=None, method="POST", **items):
        super().__init__(**items)
        self._body = body
        self.method = method
        self.app = {}

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _payload(resp) -> tuple[int, dict]:
    return resp.status, json.loads(resp.body)


@pytest.fixture
def notify(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    from navig.notify import prefs
    from navig.notify.router import get_notification_router

    return prefs, get_notification_router()


# ── Send test ────────────────────────────────────────────────────────────────


async def test_channel_test_reaches_telegram_even_when_no_type_routes_there(notify, monkeypatch):
    import navig.messaging.notify_operator as no

    prefs, _router = notify
    assert prefs.is_enabled("custom", "telegram") is False  # the default that broke it
    sent: list[str] = []
    monkeypatch.setattr(no, "notify_operator", lambda text, **kw: sent.append(text) or True)

    from navig.gateway.deck.routes.notify import handle_notify_test

    status, body = _payload(await handle_notify_test(_Req({"channel": "telegram"})))
    assert status == 200 and body["ok"] is True
    assert body["data"]["channels"] == [{"channel": "telegram", "ok": True, "detail": "sent"}]
    assert len(sent) == 1 and "Test notification" in sent[0]


async def test_channel_test_bypasses_quiet_hours_but_not_the_master_switch(notify, monkeypatch):
    import navig.messaging.notify_operator as no
    from navig.notify import router as router_mod

    prefs, router = notify
    monkeypatch.setattr(no, "notify_operator", lambda text, **kw: True)
    prefs.set_setting("quiet_hours_enabled", True)
    prefs.set_setting("quiet_hours_start", 0)
    prefs.set_setting("quiet_hours_end", 0)  # start == end → never quiet; force it below
    monkeypatch.setattr(router_mod, "_in_quiet_hours", lambda *a: True)

    res = await router.send_test("telegram")
    assert res["channels"][0]["ok"] is True

    prefs.set_setting("master_enabled", False)
    res = await router.send_test("telegram")
    assert res == {"type": "custom", "skipped": "master_off", "channels": []}


async def test_channel_test_rejects_an_unknown_channel(notify):
    from navig.gateway.deck.routes.notify import handle_notify_test

    status, body = _payload(await handle_notify_test(_Req({"channel": "pager"})))
    assert status == 400 and body["ok"] is False


async def test_a_string_false_turns_a_matrix_cell_off(notify):
    prefs, _router = notify
    from navig.gateway.deck.routes.notify import handle_notify_prefs_post

    prefs.set_cell("reminder", "telegram", True)
    status, _ = _payload(
        await handle_notify_prefs_post(_Req({"type": "reminder", "channel": "telegram", "enabled": "false"}))
    )
    assert status == 200
    assert prefs.is_enabled("reminder", "telegram") is False


# ── Per-user state resolves to the Telegram owner ────────────────────────────


@pytest.fixture
def owner(monkeypatch):
    from navig.gateway.deck.routes import telegram_bot

    users = [4242]
    monkeypatch.setattr(telegram_bot, "_allowed_telegram_users", lambda: list(users))
    return users


async def test_audio_reads_and_writes_the_owner_not_the_desktop_sentinel(owner, monkeypatch):
    from navig.gateway.channels.audio_menu import state
    from navig.gateway.deck.routes.audio import handle_deck_audio_get, handle_deck_audio_post

    seen: list[int] = []
    saved: list[int] = []
    real = state.AudioConfig()
    monkeypatch.setattr(state, "load_config", lambda uid: seen.append(uid) or real)
    monkeypatch.setattr(state, "save_config", lambda uid, cfg: saved.append(uid))

    status, _ = _payload(await handle_deck_audio_get(_Req(method="GET", deck_user_id=1)))
    assert status == 200 and seen == [4242]

    status, body = _payload(
        await handle_deck_audio_post(_Req({"voice_replies_enabled": True}, deck_user_id=1))
    )
    assert status == 200 and body["updated"] == ["voice_replies_enabled"]
    assert saved == [4242]


async def test_audio_refuses_when_the_owner_is_ambiguous(owner):
    from navig.gateway.deck.routes.audio import handle_deck_audio_get, handle_deck_audio_post

    owner[:] = [1, 2]
    status, body = _payload(await handle_deck_audio_get(_Req(method="GET", deck_user_id=1)))
    assert status == 409 and "Telegram" in body["error"]
    status, _ = _payload(await handle_deck_audio_post(_Req({"voice": "nova"}, deck_user_id=1)))
    assert status == 409


async def test_persona_switches_for_the_owner(owner, monkeypatch):
    import navig.personas.manager as manager
    import navig.personas.store as pstore
    from navig.gateway.deck.routes.persona import handle_deck_persona_get, handle_deck_persona_post

    asked: list[int] = []
    monkeypatch.setattr(pstore, "get_active_persona", lambda uid, chat_id=None: asked.append(uid) or "default")
    monkeypatch.setattr(manager, "list_personas", lambda: [])
    status, _ = _payload(await handle_deck_persona_get(_Req(method="GET", deck_user_id=1)))
    assert status == 200 and asked == [4242]

    calls: list[tuple[int, int]] = []

    class _Cfg:
        name = "tyler"
        display_name = "Tyler"
        tone = "dry"

    async def _switch(name, *, user_id, chat_id, deliver_assets, bot_client):
        calls.append((user_id, chat_id))
        return _Cfg()

    monkeypatch.setattr(manager, "switch_persona", _switch)
    status, body = _payload(await handle_deck_persona_post(_Req({"name": "tyler"}, deck_user_id=1)))
    assert status == 200 and body["active"] == "tyler"
    assert calls == [(4242, 4242)]


async def test_persona_refuses_when_the_owner_is_unknown(owner):
    from navig.gateway.deck.routes.persona import handle_deck_persona_post

    owner[:] = []
    status, _ = _payload(await handle_deck_persona_post(_Req({"name": "tyler"}, deck_user_id=1)))
    assert status == 409


async def test_a_real_telegram_user_is_still_used_as_is(owner, monkeypatch):
    from navig.gateway.channels.audio_menu import state
    from navig.gateway.deck.routes.audio import handle_deck_audio_get

    seen: list[int] = []
    monkeypatch.setattr(state, "load_config", lambda uid: seen.append(uid) or state.AudioConfig())
    await handle_deck_audio_get(_Req(method="GET", deck_user_id=777))
    assert seen == [777]


# ── Matrix settings live where the bot reads them ────────────────────────────


class _Cm:
    def __init__(self, cfg: dict):
        self.global_config = cfg

    def update_global_config(self, cfg: dict) -> None:
        self.global_config = cfg

    def save(self, *a, **kw) -> None:  # pragma: no cover — tolerated if called
        pass


@pytest.fixture
def matrix_cm(monkeypatch):
    from navig.gateway.deck.routes import social

    holder = {"cm": _Cm({})}
    monkeypatch.setattr(social, "_get_config_manager", lambda: holder["cm"])
    return holder


async def test_matrix_writes_go_to_comms_matrix(matrix_cm):
    from navig.gateway.deck.routes.social import (
        handle_deck_social_matrix_get,
        handle_deck_social_matrix_update,
    )

    status, body = _payload(
        await handle_deck_social_matrix_update(
            _Req({"enabled": True, "homeserver_url": "https://mx.example"})
        )
    )
    assert status == 200 and body["connected"] is True
    gc = matrix_cm["cm"].global_config
    assert gc["comms"]["matrix"] == {"enabled": True, "homeserver_url": "https://mx.example"}
    assert "matrix" not in gc

    status, body = _payload(await handle_deck_social_matrix_get(_Req(method="GET")))
    assert body["enabled"] is True and body["homeserver_url"] == "https://mx.example"


async def test_matrix_reads_fall_back_to_the_legacy_section_and_coerce_strings(matrix_cm):
    from navig.gateway.deck.routes.social import handle_deck_social_matrix_get

    matrix_cm["cm"] = _Cm({"matrix": {"enabled": True, "homeserver_url": "https://old"}})
    _, body = _payload(await handle_deck_social_matrix_get(_Req(method="GET")))
    assert body["enabled"] is True and body["homeserver_url"] == "https://old"

    # The real section wins, and `config set comms.matrix.enabled false` is OFF.
    matrix_cm["cm"] = _Cm({
        "comms": {"matrix": {"enabled": "false", "homeserver_url": "https://new"}},
        "matrix": {"enabled": True},
    })
    _, body = _payload(await handle_deck_social_matrix_get(_Req(method="GET")))
    assert body["enabled"] is False and body["connected"] is False
    assert body["homeserver_url"] == "https://new"
