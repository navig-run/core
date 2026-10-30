"""Each Telegram extension's "switching it off" promise is actually kept.

The extension catalog tells the operator what stops when a switch is off — and
four of those promises were only half true: switching off **Reminders** removed
the commands while every `/remindme` still arrived; **Briefings** stripped the
card buttons while the morning and evening cards kept coming; **Voice** never
gated transcription; and **Paper mail** off sent a `/courrier` letter on to the
cloud vision model it was promised it would never reach.

These drive the real delivery / dispatch code with exactly one extension off.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import navig.notify
import navig.notify.prefs as _prefs
from navig.gateway.channels import telegram_extensions as tx
from navig.gateway.channels.telegram import TelegramChannel


def _only_off(monkeypatch, ext_id: str) -> None:
    real = tx.is_enabled
    monkeypatch.setattr(tx, "is_enabled", lambda e: False if e == ext_id else real(e))


# ── Reminders ────────────────────────────────────────────────────────────────


class _Store:
    def __init__(self):
        self.completed: list[int] = []
        self.failed: list[int] = []
        self.retried: list[int] = []

    def complete_reminder(self, rid):
        self.completed.append(rid)

    def fail_reminder(self, rid):
        self.failed.append(rid)

    def increment_reminder_retry(self, rid, _delay):
        self.retried.append(rid)


class _Chan:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, parse_mode=None, keyboard=None):
        self.sent.append((chat_id, text))
        return {"message_id": 1}


class _Board:
    def __init__(self, todo_ids):
        self.todo_ids = set(todo_ids)

    def reminder_is_todo(self, rid):
        return rid in self.todo_ids


def _reminder(rid=7, chat_id=123):
    return {
        "id": rid,
        "chat_id": chat_id,
        "message": "take meds",
        "retry_count": 0,
        "remind_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture(autouse=True)
def _isolated_notify_store(tmp_path, monkeypatch):
    """The notify store remembers "reminder 7 delivered in this window".

    Without an isolated store the second test that delivers reminder 7 is collapsed by
    that marker, so a pass depended on file order. NAVIG_DATA_DIR/NAVIG_CONFIG_DIR into
    tmp (never NAVIG_HOME), `_initialised` reset so the schema re-seeds — the house
    pattern — and the assert proves the DB really is under tmp_path.
    """
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))

    from navig.notify import store as notify_store

    monkeypatch.setattr(notify_store, "_initialised", False)
    notify_store.init_db()
    assert str(notify_store._db_path()).startswith(str(tmp_path)), "notify store must be isolated"


@pytest.fixture
def _quiet_notify(monkeypatch):
    monkeypatch.setattr(_prefs, "is_enabled", lambda t, c: c == "telegram")
    monkeypatch.setattr(_prefs, "enabled_channels", lambda t: ["telegram"])

    async def _dispatch(*_a, **_k):
        return {"channels": []}

    monkeypatch.setattr(navig.notify, "dispatch", _dispatch)


def _board(monkeypatch, todo_ids=()):
    import navig.store.board as board

    monkeypatch.setattr(board, "get_board_store", lambda: _Board(todo_ids))


def test_reminders_off_holds_a_due_chat_reminder(monkeypatch, _quiet_notify):
    _only_off(monkeypatch, "reminders")
    _board(monkeypatch)
    store, ch = _Store(), _Chan()
    asyncio.run(TelegramChannel._deliver_one_reminder(ch, store, _reminder()))
    assert ch.sent == []
    # Held, not settled: the row stays due for when the switch comes back on.
    assert store.completed == [] and store.failed == [] and store.retried == []


def test_reminders_on_delivers(monkeypatch, _quiet_notify):
    _board(monkeypatch)
    store, ch = _Store(), _Chan()
    asyncio.run(TelegramChannel._deliver_one_reminder(ch, store, _reminder()))
    assert ch.sent and store.completed == [7]


def test_a_todo_reminder_follows_the_todo_switch_not_this_one(monkeypatch, _quiet_notify):
    """Reminders off must not silence a todo's ping — that is the Todo switch's job."""
    _only_off(monkeypatch, "reminders")
    _board(monkeypatch, todo_ids={7})
    store, ch = _Store(), _Chan()
    asyncio.run(TelegramChannel._deliver_one_reminder(ch, store, _reminder()))
    assert ch.sent and store.completed == [7]


def test_reminders_off_does_not_hold_a_chatless_deck_reminder(monkeypatch, _quiet_notify):
    from navig.pim.reminders import reminders_extension_holds

    _only_off(monkeypatch, "reminders")
    _board(monkeypatch)
    # The gate itself would hold it — the delivery side only asks for chat rows.
    assert reminders_extension_holds(7) is True
    store, ch = _Store(), _Chan()
    asyncio.run(TelegramChannel._deliver_one_reminder(ch, store, _reminder(chat_id=0)))
    assert ch.sent == []  # no chat to send to
    assert store.failed == []  # and not dropped by the extension gate


# ── Voice + Paper mail: drive the real update handler ───────────────────────


class _Resp:
    status = 200

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, ch):
        self.ch = ch

    def post(self, url, json=None):
        self.ch.api.append((url.rsplit("/", 1)[-1], dict(json or {})))
        return _Resp({"ok": True, "result": {"message_id": 1}})


def _channel():
    ch = TelegramChannel(bot_token="123:FAKE", allowed_users=[42], on_message=lambda *a, **k: None)
    ch.api = []
    ch._session = _Session(ch)
    return ch


def _dm(**message):
    base = {"message_id": 1, "chat": {"id": 42, "type": "private"}, "from": {"id": 42}}
    base.update(message)
    return {"message": base}


def _texts(ch):
    return [p.get("text", "") for m, p in ch.api if m == "sendMessage"]


def test_voice_off_does_not_transcribe(monkeypatch):
    import navig.gateway.channels.telegram as tg

    _only_off(monkeypatch, "voice")
    monkeypatch.setattr(tg, "HAS_VOICE", True)
    ch = _channel()
    called: list[bool] = []

    async def _transcribe(*_a, **_k):
        called.append(True)
        return "hello", "en"

    monkeypatch.setattr(ch, "_transcribe_voice_message", _transcribe)
    asyncio.run(ch._process_update(_dm(voice={"file_id": "v1", "duration": 2})))
    assert called == []
    assert any("Voice notes are switched off" in t for t in _texts(ch)), _texts(ch)


def test_paperwork_off_never_sends_the_letter_to_vision(monkeypatch):
    import navig.gateway.channels.telegram_courrier as courrier

    _only_off(monkeypatch, "paperwork")
    ch = _channel()
    intake: list[bool] = []
    vision: list[bool] = []

    async def _intake(*_a, **_k):
        intake.append(True)

    async def _vision(*_a, **_k):
        vision.append(True)

    monkeypatch.setattr(courrier, "handle_courrier_intake", _intake)
    monkeypatch.setattr(ch, "_handle_photo_vision", _vision)
    asyncio.run(
        ch._process_update(
            _dm(photo=[{"file_id": "p1", "file_size": 10}], caption="/courrier")
        )
    )
    assert intake == [] and vision == []
    assert any("Paper mail" in t for t in _texts(ch)), _texts(ch)


# ── telegram.user.enabled ────────────────────────────────────────────────────


def test_personal_account_switch_reads_string_false_as_off(monkeypatch):
    from navig.telegram import config as tcfg

    class _Cfg:
        def get(self, _k, _d=None):
            return "false"  # what `navig config set telegram.user.enabled false` stores

    monkeypatch.setattr(tcfg, "_cfg", lambda: _Cfg())
    assert tcfg.is_enabled() is False


# ── telegram.require_auth ────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [False, "false", "False", "0", "no", "off", 0])
def test_require_auth_honours_an_explicit_off(raw):
    """`navig config set telegram.require_auth false` stores the string "false"."""
    ch = TelegramChannel(bot_token="123:FAKE", require_auth=raw)
    assert ch.require_auth is False


@pytest.mark.parametrize("raw", [True, "true", "1", "yes", None, "", "garbage", "fasle"])
def test_require_auth_stays_on_unless_explicitly_off(raw):
    """A missing, empty or unreadable value must never open the bot."""
    ch = TelegramChannel(bot_token="123:FAKE", require_auth=raw)
    assert ch.require_auth is True

