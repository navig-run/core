"""Deletion watching: what is recorded, who hears about it, and how loudly.

Built from a live report — "it's flooded my navig with messages". The original
behaviour was one DM per deletion event PLUS an immediate re-send of every
photo/voice, with a single boolean to turn the whole thing off. Three decisions
were tangled into that boolean, and these tests pin them apart:

  record — the catalog row is the only surviving trace of a deleted message, so
           going quiet must not cost the record, and switching the watch OFF must
           leave no trace at all.
  mode   — instant | digest (default) | off.
  target — the owner's DM, or a separate log chat.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import navig.telegram.business as b
import navig.telegram.deletions as d


class _FakeCfg:
    def __init__(self) -> None:
        self.d: dict = {}

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value, scope=None):  # noqa: A003
        self.d[key] = value

    def save(self, scope=None):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated catalog + in-memory config, with the notify prefs gate open.

    ``should_notify`` reads the notify store (itself isolated by NAVIG_DATA_DIR);
    stubbed here so these tests are about the deletion policy, not about prefs."""
    fake = _FakeCfg()
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(b, "_cfg", lambda: fake)
    monkeypatch.setattr(d, "_cfg", lambda: fake)
    monkeypatch.setattr(b.permissions, "_cfg", lambda: fake, raising=False)
    monkeypatch.setattr(b.permissions, "business_enabled", lambda: True)
    monkeypatch.setattr(d, "should_notify", lambda: (True, ""))
    import navig.messaging.notify_operator as no
    monkeypatch.setattr(no, "resolve_operator_chat_id", lambda: "777")
    b.remember_connection("bc1", owner_id=777, can_reply=True)
    return fake


def _ch():
    ch = MagicMock()
    ch.bot_token = "8490556839:ABC"
    ch._api_call = AsyncMock(return_value={"message_id": 1})
    return ch


def _store():
    from navig.store.telegram_catalog import TelegramCatalogStore
    return TelegramCatalogStore()


def _seed(chat_id, mid, *, text="hello", sender_id=777):
    _store().upsert_message(chat_id, mid, sender_id=sender_id, sender_name="operator",
                            date="1", text=text, kind="business", raw={"business": True})


def _payload(chat_id, ids, **chat):
    return {"business_connection_id": "bc1", "message_ids": list(ids),
            "chat": {"id": chat_id, **chat}}


def _sends(ch, method="sendMessage"):
    return [c.args[1] for c in ch._api_call.call_args_list
            if c.args and c.args[0] == method]


# ── mode ──────────────────────────────────────────────────────────────────────


def test_digest_is_the_default_and_the_legacy_boolean_still_silences(env):
    assert d.mode() == "digest"          # the shipped default, after the flood report
    # An operator who had turned the original alert off stays off after upgrading —
    # waking them with a new digest would be the upgrade doing the opposite of what
    # they asked for.
    env.d[d.CFG_LEGACY_ALERT] = False
    assert d.mode() == "off"
    env.d[d.CFG_LEGACY_ALERT] = "false"  # `navig config set` stores a STRING
    assert d.mode() == "off"
    env.d[d.CFG_MODE] = "instant"        # an explicit mode wins over the legacy flag
    assert d.mode() == "instant"


def test_an_unknown_mode_falls_back_rather_than_going_silent(env):
    env.d[d.CFG_MODE] = "quiet-ish"
    assert d.mode() == "digest"          # never "off": a typo must not mute deletions


def test_set_mode_keeps_the_legacy_boolean_in_step(env):
    d.set_mode("off")
    assert env.d[d.CFG_LEGACY_ALERT] is False
    d.set_mode("digest")
    assert env.d[d.CFG_LEGACY_ALERT] is True
    with pytest.raises(ValueError):
        d.set_mode("sometimes")


# ── record: the switch the operator asked for ─────────────────────────────────


async def test_record_off_leaves_no_trace_at_all(env):
    env.d[d.CFG_RECORD] = False
    _seed(555, 1)
    ch = _ch()
    await b.handle_deleted_business_messages(ch, _payload(555, [1]))
    assert _store().get_message_by_ref(555, 1)["deleted"] is False
    assert not ch._api_call.called


async def test_record_off_accepts_the_config_string_form(env):
    # `navig config set …record false` stores the truthy string "false".
    env.d[d.CFG_RECORD] = "false"
    _seed(555, 1)
    await b.handle_deleted_business_messages(_ch(), _payload(555, [1]))
    assert _store().get_message_by_ref(555, 1)["deleted"] is False


async def test_mode_off_keeps_the_record_but_says_nothing(env):
    """The whole point of splitting record from mode: quiet, not blind."""
    env.d[d.CFG_MODE] = "off"
    _seed(555, 1, text="something private")
    ch = _ch()
    await b.handle_deleted_business_messages(ch, _payload(555, [1]))
    row = _store().get_message_by_ref(555, 1)
    assert row["deleted"] is True and row["text"] == "something private"
    assert row["deleted_at"]                      # stamped, so a digest could find it
    assert not ch._api_call.called                # and nothing was sent


# ── digest ────────────────────────────────────────────────────────────────────


async def test_digest_sends_one_card_with_a_show_button_not_a_message_per_deletion(env):
    env.d[d.CFG_MODE] = "digest"
    env.d[d.CFG_WINDOW] = 30
    ch = _ch()
    for chat, mid in ((555, 1), (555, 2), (556, 1), (556, 2)):
        _seed(chat, mid)
        await b.handle_deleted_business_messages(ch, _payload(chat, [mid]))
    # Four deletion events, and NOT four messages: the card is only sent at flush.
    assert not ch._api_call.called

    res = await d.flush_digest(ch)
    assert res["sent"] and res["count"] == 4 and res["chats"] == 2
    sent = _sends(ch)
    assert len(sent) == 1
    assert "4 deleted in 2 chats" in sent[0]["text"]     # en copy; see the parity test
    button = sent[0]["reply_markup"]["inline_keyboard"][0][0]
    assert button["text"] == "👁 Show 4"
    assert button["callback_data"].startswith("bizdel:show:")


async def test_a_digest_flush_with_nothing_pending_sends_nothing(env):
    env.d[d.CFG_MODE] = "digest"
    ch = _ch()
    res = await d.flush_digest(ch)
    assert res["sent"] is False and res["reason"] == "nothing_pending"
    assert not ch._api_call.called


async def test_the_watermark_only_advances_on_a_delivered_card(env):
    """A failed send that moved the watermark would silently drop the very window
    it was reporting."""
    env.d[d.CFG_MODE] = "digest"
    _seed(555, 1)
    ch = _ch()
    await b.handle_deleted_business_messages(ch, _payload(555, [1]))

    ch._api_call = AsyncMock(return_value=None)          # Telegram rejected it
    assert (await d.flush_digest(ch))["sent"] is False
    assert d.CFG_WATERMARK not in env.d                   # nothing consumed

    ch._api_call = AsyncMock(return_value={"message_id": 9})
    assert (await d.flush_digest(ch))["sent"] is True
    assert env.d[d.CFG_WATERMARK]                         # now it moves


async def test_quiet_hours_hold_the_window_instead_of_eating_it(env, monkeypatch):
    env.d[d.CFG_MODE] = "digest"
    _seed(555, 1)
    ch = _ch()
    await b.handle_deleted_business_messages(ch, _payload(555, [1]))
    monkeypatch.setattr(d, "should_notify", lambda: (False, "quiet hours"))
    res = await d.flush_digest(ch)
    assert res["sent"] is False and res["reason"] == "quiet hours"
    assert d.CFG_WATERMARK not in env.d     # reported once quiet hours lift, not lost
    # …and the operator can still ask for it directly.
    assert (await d.flush_digest(ch, force=True))["sent"] is True


async def test_the_show_button_renders_the_same_detail_the_instant_alert_would(env):
    env.d[d.CFG_MODE] = "digest"
    _store().upsert_room(555, type="business", title="Sam")
    _seed(555, 1, text="see you at 9")
    _seed(555, 2, text="ok", sender_id=555)
    ch = _ch()
    await b.handle_deleted_business_messages(ch, _payload(555, [1, 2], first_name="Sam"))
    await d.flush_digest(ch)
    token = _sends(ch)[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

    ch._api_call = AsyncMock(return_value={"message_id": 2})
    toast = await d.handle_callback(ch, token, chat_id=777, message_id=5, user_id=777)
    assert toast == "Showed 2"
    detail = _sends(ch)[0]["text"]
    assert "In Sam:" in detail
    assert "see you at 9" in detail and "ok" in detail


async def test_the_quiet_button_turns_alerts_off_and_says_where_the_record_lives(env):
    ch = _ch()
    toast = await d.handle_callback(ch, "bizdel:off", chat_id=777, message_id=5, user_id=777)
    assert toast == "Deletion alerts off"
    assert d.mode() == "off"
    assert d.record_enabled() is True          # quiet, not blind
    assert "navig telegram business deleted" in _sends(ch)[0]["text"]


async def test_an_unknown_callback_is_not_treated_as_an_action(env):
    ch = _ch()
    assert await d.handle_callback(ch, "bizdel:nonsense", 777, 5, 777) == ""
    assert not ch._api_call.called


def test_a_junk_show_token_falls_back_to_a_window_rather_than_crashing(env):
    since = d.since_from_token("not-a-number")
    assert since.endswith("Z") and len(since) >= 20


# ── per-chat mute ─────────────────────────────────────────────────────────────


async def test_a_muted_chat_is_recorded_but_never_announced(env):
    env.d[d.CFG_MODE] = "instant"
    d.set_muted(555, True)
    _seed(555, 1)
    _seed(556, 1)
    ch = _ch()
    await b.handle_deleted_business_messages(ch, _payload(555, [1]))
    assert _store().get_message_by_ref(555, 1)["deleted"] is True
    assert not ch._api_call.called          # muted chat: silent

    await b.handle_deleted_business_messages(ch, _payload(556, [1]))
    assert _sends(ch)                        # another chat still reports

    assert d.set_muted(555, False) is False
    assert d.muted_chats() == set()


def test_muted_chats_accepts_the_config_string_form(env):
    env.d[d.CFG_MUTED] = "-100123, 456"      # what `navig config set` stores
    assert d.muted_chats() == {-100123, 456}
    assert d.is_muted(456) and not d.is_muted(1)
    assert d.is_muted("nope") is False       # junk is not "muted"


# ── target: the log chat (why a second bot is not needed) ─────────────────────


async def test_a_log_chat_receives_the_report_instead_of_the_owner_dm(env):
    env.d[d.CFG_MODE] = "instant"
    d.set_target_chat("-1001234567890")
    _seed(555, 1, text="private")
    ch = _ch()
    await b.handle_deleted_business_messages(ch, _payload(555, [1]))
    sent = _sends(ch)
    assert sent and sent[0]["chat_id"] == "-1001234567890"   # not the owner's DM


async def test_clearing_the_target_goes_back_to_the_owner_dm(env):
    env.d[d.CFG_MODE] = "instant"
    d.set_target_chat("-100123")
    d.set_target_chat(None)
    assert d.target_chat() is None
    _seed(555, 1)
    ch = _ch()
    await b.handle_deleted_business_messages(ch, _payload(555, [1]))
    assert _sends(ch)[0]["chat_id"] == "777"


# ── window bounds ─────────────────────────────────────────────────────────────


def test_the_window_is_clamped_and_tolerates_a_string(env):
    assert d.window_sec() == d.DEFAULT_WINDOW_SEC
    env.d[d.CFG_WINDOW] = "60"               # config strings again
    assert d.window_sec() == 60
    env.d[d.CFG_WINDOW] = 1                  # too tight to batch anything
    assert d.window_sec() == d.MIN_WINDOW_SEC
    env.d[d.CFG_WINDOW] = 10**9
    assert d.window_sec() == d.MAX_WINDOW_SEC
    env.d[d.CFG_WINDOW] = "not a number"
    assert d.window_sec() == d.DEFAULT_WINDOW_SEC
    with pytest.raises(ValueError):
        d.set_window_sec(0)


def test_status_reports_every_switch_in_one_read(env):
    env.d[d.CFG_MODE] = "instant"
    d.set_target_chat("-100999")
    d.set_muted(42, True)
    st = d.status()
    assert st == {"record": True, "mode": "instant", "window_sec": d.DEFAULT_WINDOW_SEC,
                  "target": "-100999", "muted_chats": [42]}


# ── CLI: the ids these commands exist for are all negative ────────────────────


def _deletions_cli():
    """The real `navig telegram business deletions` app, wired the way the CLI does."""
    import typer

    from navig.commands._telegram_mtproto import register

    telegram_app = typer.Typer()
    register(telegram_app)
    for group in telegram_app.registered_groups:
        if group.name == "business":
            for sub in group.typer_instance.registered_groups:
                if sub.name == "deletions":
                    return sub.typer_instance
    raise AssertionError("the `deletions` command group is not registered")


@pytest.mark.parametrize("command", ["target", "mute"])
def test_a_negative_chat_id_is_accepted_not_read_as_an_option(env, command):
    """EVERY Telegram group/channel id is negative, and Click reads a leading "-"
    as an option: `deletions target -1001234567890` failed with "No such option:
    -1", so both commands were unusable for exactly the ids they take. Found by
    running them, not by reading them."""
    from typer.testing import CliRunner

    res = CliRunner().invoke(_deletions_cli(), [command, "-1001234567890"])
    assert res.exit_code == 0, res.output
    assert "No such option" not in res.output


def test_a_non_numeric_target_is_rejected_with_a_usable_message(env):
    from typer.testing import CliRunner

    res = CliRunner().invoke(_deletions_cli(), ["target", "my-channel"])
    assert res.exit_code == 1
    assert "numeric chat id" in res.output


def test_target_dm_clears_the_log_chat(env):
    from typer.testing import CliRunner

    d.set_target_chat("-100123")
    res = CliRunner().invoke(_deletions_cli(), ["target", "dm"])
    assert res.exit_code == 0 and d.target_chat() is None


def test_record_off_warns_that_it_leaves_no_trace_and_names_the_quiet_option(env):
    """Turning tracking off is not the same as going quiet, and the difference is
    a data loss the operator has to be told about at the moment they choose it."""
    from typer.testing import CliRunner

    res = CliRunner().invoke(_deletions_cli(), ["record", "off"])
    assert res.exit_code == 0
    assert "no trace" in res.output
    assert "deletions mode off" in res.output      # the option they probably wanted
    assert d.record_enabled() is False
