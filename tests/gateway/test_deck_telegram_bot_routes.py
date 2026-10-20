"""Contract tests for the Mini App's Telegram-bot preference routes.

Settings → Telegram must change exactly what the bot's chat commands change:

* language      → ``user.language``, the store ``/lang`` writes and reads back;
* voice replies → the per-user AudioConfig AND the per-chat ``/voiceoff`` override
  cleared, because the send path lets a per-chat value win;
* model tier    → the bot's own ``_set_user_tier_pref`` (memory + DM session), so a
  choice survives a restart and lands on the real user, not the Bearer sentinel;
* focus mode    → ``/api/deck/mode`` accepts exactly ``FOCUS_MODES``;
* /help tree    → every visible registry command exactly once, liveness from
  ``command_is_live`` (the predicate the bot uses).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.deck.routes import telegram_bot

#: What the auth middleware stamps for a Bearer / local-desktop request.
SENTINEL = 1


def _app(user_id: int = SENTINEL, gateway=None) -> web.Application:
    @web.middleware
    async def _stamp(request, handler):
        request["deck_user_id"] = user_id
        return await handler(request)

    app = web.Application(middlewares=[_stamp])
    if gateway is not None:
        app["gateway"] = gateway
    telegram_bot.register(app)

    from navig.gateway.deck.routes.core import handle_deck_mode
    from navig.gateway.deck.routes.models import handle_deck_models, handle_deck_models_set

    app.router.add_post("/api/deck/mode", handle_deck_mode)
    app.router.add_get("/api/deck/models", handle_deck_models)
    app.router.add_post("/api/deck/models", handle_deck_models_set)
    return app


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Fresh config dir, audio store and session store for each test."""
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton
    from navig.gateway.channels import telegram_sessions
    from navig.gateway.channels.audio_menu import state as audio_state

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    monkeypatch.setattr(audio_state, "_STORE_DIR", tmp_path / "audio")
    monkeypatch.setattr(audio_state, "_cache", {})
    sm = telegram_sessions.SessionManager(storage_dir=tmp_path / "sessions")
    monkeypatch.setattr(telegram_sessions, "_session_manager", sm)
    yield sm
    reset_config_manager()
    ConfigSingleton._instance = None


def _set(key: str, value) -> None:
    from navig.config import ConfigManager

    ConfigManager().set_global(key, value)


def _fresh_language():
    from navig.config import ConfigManager
    from navig.core.language import CFG_LANGUAGE, normalise_language

    # The exact read /lang performs.
    return normalise_language(ConfigManager().get(CFG_LANGUAGE, ""))


async def _post(client, path, body):
    res = await client.post(path, json=body)
    return res.status, await res.json()


async def _get(client, path):
    res = await client.get(path)
    return res.status, await res.json()


# ── language ─────────────────────────────────────────────────────────────────


async def test_language_round_trips_through_the_lang_store(isolated):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/telegram/bot/language", {"language": "Russian"})
        assert status == 200, body
        assert body["language"] == "Russian"
        assert _fresh_language() == "Russian"

        status, body = await _get(client, "/api/deck/telegram/bot/preferences")
        assert status == 200
        assert body["language"] == "Russian"
        assert any(o["value"] == "auto" for o in body["options"])


async def test_language_accepts_an_iso_code_and_stores_the_name(isolated):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/telegram/bot/language", {"language": "fr"})
        assert status == 200, body
        assert body["language"] == "French"
        assert _fresh_language() == "French"


async def test_language_auto_unpins(isolated):
    _set("user.language", "Russian")
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/telegram/bot/language", {"language": "auto"})
        assert status == 200, body
        assert body["language"] == "auto"
        assert _fresh_language() is None


async def test_language_rejects_unknown_values_without_writing(isolated):
    _set("user.language", "English")
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/telegram/bot/language", {"language": "Klingon"})
        assert status == 400
        assert body["ok"] is False
        status, _ = await _post(client, "/api/deck/telegram/bot/language", {})
        assert status == 400
    assert _fresh_language() == "English"


async def test_language_set_via_lang_outside_the_list_is_still_shown(isolated):
    _set("user.language", "Swahili")
    async with TestClient(TestServer(_app())) as client:
        _, body = await _get(client, "/api/deck/telegram/bot/preferences")
    assert body["language"] == "Swahili"
    assert any(o["value"] == "Swahili" for o in body["options"])


# ── voice replies ────────────────────────────────────────────────────────────


async def test_voice_on_clears_a_stale_voiceoff_override(isolated):
    owner = 4242
    isolated.set_session_metadata(owner, owner, "voice_replies_enabled", False)
    async with TestClient(TestServer(_app(user_id=owner))) as client:
        status, body = await _post(client, "/api/deck/telegram/bot/voice-replies", {"enabled": True})
        assert status == 200, body
        assert body["enabled"] is True
        assert body["chat_override"] is None

    assert isolated.get_session_metadata(owner, owner, "voice_replies_enabled", None) is None
    from navig.gateway.channels.audio_menu import state as audio_state

    audio_state._cache.clear()  # prove it hit disk, not just the cache
    assert audio_state.load_config(owner).voice_replies_enabled is True


async def test_voice_does_not_create_a_session_for_a_user_who_never_chatted(isolated):
    async with TestClient(TestServer(_app(user_id=555))) as client:
        status, body = await _post(client, "/api/deck/telegram/bot/voice-replies", {"enabled": True})
    assert status == 200, body
    assert isolated._sessions == {}


async def test_voice_refuses_when_the_owner_is_ambiguous(isolated):
    async with TestClient(TestServer(_app())) as client:  # Bearer, no allowed_users
        status, body = await _post(client, "/api/deck/telegram/bot/voice-replies", {"enabled": True})
        assert status == 409
        _, prefs = await _get(client, "/api/deck/telegram/bot/preferences")
        assert prefs["voice_replies"] is None
        assert "voice_replies_error" in prefs

    _set("telegram.allowed_users", [11, 22])
    async with TestClient(TestServer(_app())) as client:
        status, _ = await _post(client, "/api/deck/telegram/bot/voice-replies", {"enabled": True})
        assert status == 409


async def test_voice_under_bearer_writes_for_the_single_owner(isolated):
    _set("telegram.allowed_users", [77])
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/telegram/bot/voice-replies", {"enabled": True})
        assert status == 200, body
        status, _ = await _post(client, "/api/deck/telegram/bot/voice-replies", {"enabled": "yes"})
        assert status == 400
    from navig.gateway.channels.audio_menu import state as audio_state

    audio_state._cache.clear()
    assert audio_state.load_config(77).voice_replies_enabled is True
    assert audio_state.load_config(SENTINEL).voice_replies_enabled is False


# ── model tier ───────────────────────────────────────────────────────────────


class _FakeTelegram:
    def __init__(self):
        self._user_model_prefs: dict[int, str] = {}
        self.calls: list[tuple] = []

    def _set_user_tier_pref(self, chat_id, user_id, tier, *, is_group=None, persist=True):
        self.calls.append((chat_id, user_id, tier, is_group))
        self._user_model_prefs[user_id] = tier

    def _get_user_tier_pref(self, chat_id, user_id, *, is_group=None):
        return self._user_model_prefs.get(user_id, "")


class _FakeGateway:
    def __init__(self, tg):
        self.channels = {"telegram": tg}


async def test_model_tier_persists_through_the_bots_own_writer(isolated):
    _set("telegram.allowed_users", [77])
    tg = _FakeTelegram()
    async with TestClient(TestServer(_app(gateway=_FakeGateway(tg)))) as client:
        status, body = await _post(client, "/api/deck/models", {"tier": "big"})
        assert status == 200
        assert body["ok"] is True
        _, got = await _get(client, "/api/deck/models")
    # Keyed on the real owner (77), never the Bearer sentinel (1).
    assert tg.calls == [(77, 77, "big", False)]
    assert got["user_tier_override"] == "big"


# ── focus mode ───────────────────────────────────────────────────────────────


async def test_mode_accepts_exactly_focus_modes(isolated, monkeypatch):
    from navig.gateway.channels.focus_modes import FOCUS_MODES
    from navig.gateway.deck.routes import core as core_routes

    saved: list[str] = []

    class _Tracker:
        def set_preference(self, key, value):
            saved.append(value)
            return True

    monkeypatch.setattr(core_routes, "_get_tracker", lambda: _Tracker())
    async with TestClient(TestServer(_app())) as client:
        for mode in FOCUS_MODES:
            status, body = await _post(client, "/api/deck/mode", {"mode": mode})
            assert status == 200, (mode, body)
        status, _ = await _post(client, "/api/deck/mode", {"mode": "balance"})
        assert status == 400
    assert saved == list(FOCUS_MODES)


async def test_mode_reports_a_rejected_write(isolated, monkeypatch):
    from navig.gateway.deck.routes import core as core_routes

    class _Tracker:
        def set_preference(self, key, value):
            return False

    monkeypatch.setattr(core_routes, "_get_tracker", lambda: _Tracker())
    async with TestClient(TestServer(_app())) as client:
        status, _ = await _post(client, "/api/deck/mode", {"mode": "work"})
    assert status == 500


# ── /help tree ───────────────────────────────────────────────────────────────


def _names(tree) -> list[str]:
    out: list[str] = []
    for cat in tree["categories"]:
        for c in cat.get("commands", []):
            out.append(c["name"])
        for sub in cat.get("subcategories", []):
            out.extend(c["name"] for c in sub["commands"])
    return out


def test_help_tree_lists_every_visible_command_exactly_once(isolated):
    from navig.gateway.channels.telegram_commands import _HELP_CATEGORIES, _iter_unique_registry

    tree = telegram_bot.build_help_tree()
    names = _names(tree)
    assert len(names) == len(set(names)), "a command is listed twice"

    visible = {e.command.lower() for e in _iter_unique_registry(visible_only=True)}
    assert visible <= set(names), f"missing from the tree: {sorted(visible - set(names))}"

    keys = [c["key"] for c in tree["categories"]]
    assert keys[: len(_HELP_CATEGORIES)] == [c.key for c in _HELP_CATEGORIES]
    # Every visible command now has a hand-written home, so there is no "More" bucket.
    # The route keeps that logic for future drift; this pins today's state.
    assert "more" not in keys
    assert "lang" in names


def test_help_tree_still_buckets_drift_into_more(isolated, monkeypatch):
    """The "More" fallback still works the day a command ships without a category."""
    from navig.gateway.channels import telegram_commands as tc

    trimmed = [c for c in tc._HELP_CATEGORIES if c.key != "mesh"]
    monkeypatch.setattr(tc, "_HELP_CATEGORIES", trimmed)
    tree = telegram_bot.build_help_tree()
    more = next(c for c in tree["categories"] if c["key"] == "more")
    assert "nodes" in [c["name"] for s in more["subcategories"] for c in s["commands"]]


def test_help_tree_flags_a_disabled_command_as_not_live(isolated):
    _set("telegram.disabled_commands", ["weather"])
    tree = telegram_bot.build_help_tree()
    flags = {}
    for cat in tree["categories"]:
        for c in cat.get("commands", []):
            flags[c["name"]] = c["live"]
        for sub in cat.get("subcategories", []):
            for c in sub["commands"]:
                flags[c["name"]] = c["live"]
    assert flags["weather"] is False
    assert flags["help"] is True  # locked commands are always live


async def test_help_route_serves_the_tree(isolated):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _get(client, "/api/deck/telegram/bot/help")
    assert status == 200
    assert body["ok"] is True
    assert body["categories"]


# ── weigh-in cadence ─────────────────────────────────────────────────────────


async def test_weigh_in_defaults_then_round_trip(isolated):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _get(client, "/api/deck/telegram/bot/weigh-in")
        assert status == 200
        assert (body["enabled"], body["every_days"], body["weekdays"], body["latest_hour"]) == (
            True, 1, [], 24,
        )
        status, body = await _post(
            client,
            "/api/deck/telegram/bot/weigh-in",
            {"every_days": 3, "weekdays": ["thu", "mon"], "latest_hour": 12},
        )
        assert status == 200, body
        assert (body["every_days"], body["weekdays"], body["latest_hour"]) == (3, ["mon", "thu"], 12)

    # What the scheduled `navig body checkin --send` reads next.
    from navig.spaces import weigh_cadence as wc

    s = wc.load_settings()
    assert (s.every_days, s.weekday_names(), s.latest_hour) == (3, ["mon", "thu"], 12)


async def test_weigh_in_can_be_switched_off(isolated):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(client, "/api/deck/telegram/bot/weigh-in", {"enabled": False})
    assert status == 200, body
    from navig.spaces import weigh_cadence as wc

    assert wc.load_settings().enabled is False


async def test_weigh_in_rejects_bad_values_without_writing(isolated):
    async with TestClient(TestServer(_app())) as client:
        for bad in (
            {"every_days": 0},
            {"every_days": 31},
            {"every_days": "3"},
            {"every_days": True},
            {"latest_hour": 0},
            {"latest_hour": 25},
            {"weekdays": ["funday"]},
            {"enabled": "yes"},
            {},
        ):
            status, body = await _post(client, "/api/deck/telegram/bot/weigh-in", bad)
            assert status == 400, (bad, body)
            assert body["ok"] is False
    from navig.spaces import weigh_cadence as wc

    assert wc.load_settings() == wc.WeighInSettings()
