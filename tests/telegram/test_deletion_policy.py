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
                  "target": "-100999", "muted_chats": [42],
                  "quiet_expired": True, "quiet_wipes": True}


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


# ── the original on/off switch must still mean what it says ───────────────────


def test_alerts_off_works_after_a_mode_was_set_explicitly(env):
    """The bug: `business alerts off` wrote only the legacy boolean, and an explicit
    mode (set by `deletions mode …` or the card's Quiet button) wins over it — so
    `alerts off` silently did nothing once anyone had touched the new switches."""
    d.set_mode("digest")                 # an explicit mode is now in config
    b.set_deletion_alert(False)
    assert d.mode() == "off"
    assert b.deletion_alert_enabled() is False


def test_alerts_on_restores_the_default_only_from_off(env):
    d.set_mode("off")
    b.set_deletion_alert(True)
    assert d.mode() == d.DEFAULT_MODE    # digest


def test_alerts_on_keeps_an_explicit_instant_choice(env):
    d.set_mode("instant")
    b.set_deletion_alert(True)
    assert d.mode() == "instant"


def test_the_legacy_flag_is_read_through_coerce_bool(env):
    """`navig config set …deletion_alert false` stores the STRING "false"; the old
    `bool()` read reported that as ON."""
    env.d[d.CFG_LEGACY_ALERT] = "false"
    assert b.deletion_alert_enabled() is False


def test_the_alerts_cli_drives_the_mode(env):
    import typer
    from typer.testing import CliRunner

    from navig.commands._telegram_mtproto import register

    app = typer.Typer()
    register(app)
    business = next(g.typer_instance for g in app.registered_groups if g.name == "business")
    d.set_mode("digest")
    res = CliRunner().invoke(business, ["alerts", "off"])
    assert res.exit_code == 0, res.output
    assert "off" in res.output and d.mode() == "off"
    res = CliRunner().invoke(business, ["alerts", "on"])
    assert res.exit_code == 0 and d.mode() == "digest"


# ── clearing what Show revealed ───────────────────────────────────────────────
#
# Asked 2026-09-27, under a Show that re-posted two deleted videos: "I want to be
# able to delete the messages when I press Show". Show posts a report and every
# cached file back into the chat — two videos are three messages — and nothing
# recorded which ones, so nothing could offer to take them away again.


class _Numbered:
    """A channel whose every send gets its own message id, like Telegram's."""

    def __init__(self, refuse_delete: set[int] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.ids: list[int | None] = []  # the id each call produced, aligned with calls
        self.next_id = 100
        self.refuse = refuse_delete or set()

    async def _api_call(self, method, data=None):
        data = data or {}
        self.calls.append((method, data))
        if method == "deleteMessage":
            self.ids.append(None)
            return None if data.get("message_id") in self.refuse else True
        self.next_id += 1
        self.ids.append(self.next_id)
        return {"message_id": self.next_id}

    def sent(self, method=None):
        return [(m, p) for m, p in self.calls if method is None or m == method]


def _seed_video(chat_id, mid, file_id):
    store = _store()
    media_id = store.upsert_media(
        chat_id,
        message_id=mid,
        file_id=file_id,
        file_unique_id=f"u-{file_id}",
        kind="video",
    )
    store.upsert_message(
        chat_id,
        mid,
        sender_id=777,
        sender_name="operator",
        date="1",
        text=None,
        kind="business",
        media_ref=media_id,
        raw={"business": True},
    )


@pytest.fixture
def reveals(tmp_path, monkeypatch):
    path = tmp_path / "deletion_reveals.json"
    monkeypatch.setattr(d, "_reveals_path", lambda: path)
    return path


async def _show_two_videos(env, ch):
    env.d[d.CFG_MODE] = "digest"
    for mid, fid in ((1, "VID-A"), (2, "VID-B")):
        _seed_video(555, mid, fid)
        await b.handle_deleted_business_messages(ch, _payload(555, [mid], title="Уск"))
    await d.flush_digest(ch)
    card = ch.sent("sendMessage")[-1][1]
    show = card["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    before = len(ch.calls)
    toast = await d.handle_callback(ch, show, "777", 50, 777)
    return toast, ch.calls[before:], ch.ids[before:]


async def test_show_ends_with_one_button_that_clears_what_it_posted(env, reveals):
    ch = _Numbered()
    toast, posted, _ = await _show_two_videos(env, ch)

    assert toast.startswith("Showed 2"), toast
    videos = [p for m, p in posted if m == "sendVideo"]
    assert {v["video"] for v in videos} == {"VID-A", "VID-B"}, "both videos came back"
    control = posted[-1][1]
    button = control["reply_markup"]["inline_keyboard"][0][0]
    assert button["callback_data"].startswith("bizdel:clr:"), button
    assert len(button["callback_data"].encode()) <= 64
    assert "3" in button["text"], "the report + two videos = three messages to clear"


async def test_clear_removes_exactly_what_show_posted_and_the_button(env, reveals):
    ch = _Numbered()
    _, _, ids = await _show_two_videos(env, ch)
    *revealed, control_id = ids  # the report, two videos, then the button message
    clr = ch.sent("sendMessage")[-1][1]["reply_markup"]["inline_keyboard"][0][0][
        "callback_data"
    ]

    toast = await d.handle_callback(ch, clr, "777", control_id, 777)

    deleted = [p["message_id"] for m, p in ch.calls if m == "deleteMessage"]
    assert deleted == [*revealed, control_id], f"removed {deleted}, Show posted {ids}"
    assert 50 not in deleted, "the digest card stays — it can be shown again"
    assert toast == "Cleared 3"


async def test_a_second_clear_tap_is_harmless(env, reveals):
    ch = _Numbered()
    await _show_two_videos(env, ch)
    clr = ch.sent("sendMessage")[-1][1]["reply_markup"]["inline_keyboard"][0][0][
        "callback_data"
    ]
    await d.handle_callback(ch, clr, "777", ch.next_id, 777)
    deletes_after_first = sum(1 for m, _ in ch.calls if m == "deleteMessage")

    toast = await d.handle_callback(ch, clr, "777", ch.next_id, 777)

    assert toast == "Already cleared"
    assert sum(1 for m, _ in ch.calls if m == "deleteMessage") == deletes_after_first


async def test_a_clear_says_so_when_telegram_keeps_some(env, reveals):
    """A bot can only delete its messages for 48 hours. Past that, the toast has
    to say how many stayed — "Cleared" over a chat still holding them is a lie."""
    ch = _Numbered()
    await _show_two_videos(env, ch)
    videos = [p for m, p in ch.calls if m == "sendVideo"]
    assert videos
    ch.refuse = {ch.next_id - 1}  # the last video refuses
    clr = ch.sent("sendMessage")[-1][1]["reply_markup"]["inline_keyboard"][0][0][
        "callback_data"
    ]

    toast = await d.handle_callback(ch, clr, "777", ch.next_id, 777)

    assert toast.startswith("Cleared 2 of 3"), toast


async def test_a_clear_key_from_another_chat_removes_nothing(env, reveals):
    """The key is only a message id, and ids repeat across chats."""
    d._remember_reveal("777", "101", [101, 102])
    ch = _Numbered()

    toast = await d.handle_callback(ch, "bizdel:clr:101", "999", 5, 999)

    assert toast == "Already cleared"
    assert not [m for m, _ in ch.calls if m == "deleteMessage"]


# ── deleting the card itself ──────────────────────────────────────────────────
#
# Asked 2026-09-28 under a digest card: "it says Тихо, but what does it do? Can we
# just have a button to delete it". Тихо switched EVERY future alert off; there was
# no way to remove THIS card. Delete is now its own button, and the switch says what
# it switches.


async def _card(env):
    env.d[d.CFG_MODE] = "digest"
    _seed(555, 1)
    ch = _ch()
    await b.handle_deleted_business_messages(ch, _payload(555, [1]))
    await d.flush_digest(ch)
    return ch, _sends(ch)[-1]["reply_markup"]["inline_keyboard"]


async def test_the_card_has_a_delete_button_beside_show(env):
    _, rows = await _card(env)
    first = [btn["callback_data"] for btn in rows[0]]
    assert first[0].startswith("bizdel:show:")
    assert "bizdel:del" in first, f"no delete button on the card: {rows}"


async def test_the_off_switch_says_what_it_switches_on_its_own_row(env):
    _, rows = await _card(env)
    off = [btn for row in rows for btn in row if btn["callback_data"] == "bizdel:off"]
    assert off and len(rows[-1]) == 1 and rows[-1][0] is off[0], (
        "kept apart from delete"
    )
    assert off[0]["text"] != "🔕 Quiet", "a one-word label is what made it unclear"


async def test_delete_removes_this_card_and_changes_nothing_else(env):
    ch = _ch()
    ch._api_call = AsyncMock(return_value=True)
    toast = await d.handle_callback(
        ch, "bizdel:del", chat_id=777, message_id=42, user_id=777
    )

    calls = [(c.args[0], c.args[1]) for c in ch._api_call.call_args_list]
    assert calls == [("deleteMessage", {"chat_id": 777, "message_id": 42})]
    assert toast == "Deleted"
    assert d.mode() == "digest", "deleting a card must not switch alerts off"


async def test_a_card_too_old_to_delete_says_so(env):
    ch = _ch()
    ch._api_call = AsyncMock(return_value=None)  # Telegram refuses past 48 h
    toast = await d.handle_callback(
        ch, "bizdel:del", chat_id=777, message_id=42, user_id=777
    )
    assert "48" in toast, toast


# ── NAVIG's own deletions stay out of the record ──────────────────────────────


async def test_a_deletion_navig_made_itself_is_not_recorded_or_reported(
    env, tmp_path, monkeypatch
):
    """The owner's `tyler` trigger is deleted by the bot; Telegram reports that
    deletion like any other. It is not news — no record, no digest line."""
    monkeypatch.setattr(d, "_own_deletions_path", lambda: tmp_path / "own.json")
    env.d[d.CFG_MODE] = "digest"
    _seed(555, 1, text="tyler")
    _seed(555, 2, text="a real message")
    d.note_own_deletion("bc1", [1])

    ch = _ch()
    await b.handle_deleted_business_messages(ch, _payload(555, [1, 2]))

    assert _store().get_message_by_ref(555, 1)["deleted"] is False, (
        "our trigger is not a deletion"
    )
    assert _store().get_message_by_ref(555, 2)["deleted"] is True, "a real one still is"
