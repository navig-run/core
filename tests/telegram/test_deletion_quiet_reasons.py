"""Auto-delete expiries and whole-chat wipes are recorded but not announced.

Measured on the operator's catalog (2026-10-09): 604 of 716 business deletions
landed within ten minutes of an exact multiple of 24 h after the message was sent —
a chat's auto-delete timer, not a person. A whole-history clear arrives as one
update carrying every id. Both used to fill the digest; both must stay viewable.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import navig.telegram.business as b
import navig.telegram.deletions as d

DAY = 86_400


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
    fake = _FakeCfg()
    fake.d[d.CFG_MODE] = "digest"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NAVIG_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(b, "_cfg", lambda: fake)
    monkeypatch.setattr(d, "_cfg", lambda: fake)
    monkeypatch.setattr(b.permissions, "_cfg", lambda: fake, raising=False)
    monkeypatch.setattr(b.permissions, "business_enabled", lambda: True)
    monkeypatch.setattr(d, "should_notify", lambda: (True, ""))
    import navig.messaging.notify_operator as no
    monkeypatch.setattr(no, "resolve_operator_chat_id", lambda: "777")
    b.remember_connection("bc1", owner_id=777, can_reply=True)
    armed: list = []
    monkeypatch.setattr(d, "arm_digest", lambda ch, delay=None: armed.append(delay))
    return fake, armed


def _ch():
    ch = MagicMock()
    ch.bot_token = "8490556839:ABC"
    ch._api_call = AsyncMock(return_value={"message_id": 1})
    return ch


def _store():
    from navig.store.telegram_catalog import TelegramCatalogStore
    return TelegramCatalogStore()


def _msg(chat_id, mid, *, sent):
    _store().upsert_message(chat_id, mid, sender_id=42, sender_name="them",
                            date=str(int(sent)), text=f"m{mid}", kind="business",
                            raw={"business": True})


async def _delete(chat_id, ids):
    await b.handle_deleted_business_messages(
        _ch(), {"business_connection_id": "bc1", "message_ids": ids, "chat": {"id": chat_id}})


def _reason(chat_id, mid):
    return _store().get_message_by_ref(chat_id, mid)["deleted_reason"]


# ── the classifier ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("life,expected", [
    (DAY, True), (DAY + 60, True), (DAY - 60, True),       # 1-day timer, ±1 min
    (3 * DAY + 4 * 60, True), (7 * DAY, True),              # 3 days, a week
    (DAY + 30 * 60, False), (DAY - 30 * 60, False),         # a person, near a day
    (5 * 60, False), (0, False),                            # quick regret
])
def test_looks_expired_is_whole_days_only(life, expected):
    now = 1_800_000_000
    assert d.looks_expired(str(now - life), now) is expected


@pytest.mark.parametrize("junk", ["", "2026-09-27T10:00:00Z", None, "0"])
def test_an_unreadable_send_date_is_an_ordinary_deletion(junk):
    assert d.looks_expired(junk, time.time()) is False


# ── the pipeline: recorded, not announced ─────────────────────────────────────


async def test_a_timer_expiry_is_recorded_but_does_not_arm_the_digest(env):
    _, armed = env
    _msg(555, 1, sent=time.time() - DAY)
    await _delete(555, [1])
    assert _reason(555, 1) == d.REASON_EXPIRED
    assert armed == []
    assert _store().list_deleted(kind="business")[0]["message_id"] == 1   # still viewable


async def test_an_ordinary_deletion_still_arms_the_digest(env):
    _, armed = env
    _msg(555, 1, sent=time.time() - 3600)
    _msg(555, 2, sent=time.time() - 60)       # keeps the chat non-empty
    await _delete(555, [1])
    assert _reason(555, 1) is None
    assert armed == [None]


async def test_a_large_batch_is_a_wipe(env):
    _, armed = env
    for mid in range(1, 13):
        _msg(555, mid, sent=time.time() - 3600)
    _msg(555, 99, sent=time.time() - 60)      # not every row: size alone decides
    await _delete(555, list(range(1, 13)))
    assert {_reason(555, m) for m in range(1, 13)} == {d.REASON_WIPED}
    assert armed == []


async def test_a_small_batch_that_empties_the_chat_is_a_wipe(env):
    _, armed = env
    for mid in (1, 2, 3):
        _msg(555, mid, sent=time.time() - 3600)
    await _delete(555, [1, 2, 3])
    assert {_reason(555, m) for m in (1, 2, 3)} == {d.REASON_WIPED}
    assert armed == []


async def test_a_small_batch_that_leaves_messages_is_not_a_wipe(env):
    _, armed = env
    for mid in (1, 2, 3, 4):
        _msg(555, mid, sent=time.time() - 3600)
    await _delete(555, [1, 2, 3])
    assert {_reason(555, m) for m in (1, 2, 3)} == {None}
    assert armed == [None]


async def test_a_redelivered_deletion_keeps_its_first_reason(env):
    _msg(555, 1, sent=time.time() - DAY)
    await _delete(555, [1])
    _store().mark_message_deleted(555, 1, reason=None)
    assert _reason(555, 1) == d.REASON_EXPIRED


# ── the digest never counts them; switching it off brings them back ───────────


async def test_the_digest_count_and_show_exclude_quiet_rows(env):
    _msg(555, 1, sent=time.time() - DAY)          # expiry
    _msg(555, 2, sent=time.time() - 3600)         # a person
    _msg(555, 3, sent=time.time() - 60)
    await _delete(555, [1])
    await _delete(555, [2])
    since = "2000-01-01T00:00:00.000Z"
    assert _store().count_deleted_since(since, **d._digest_scope())["messages"] == 1
    shown = _store().list_deleted(since=since, **d._digest_scope())
    assert [r["message_id"] for r in shown] == [2]
    # The browse surface (CLI `deleted`, deck) passes no reason filter: both visible.
    assert len(_store().list_deleted(kind="business")) == 2


async def test_quiet_off_announces_expiries_again(env):
    _, armed = env
    d.set_quiet(d.REASON_EXPIRED, False)
    _msg(555, 1, sent=time.time() - DAY)
    _msg(555, 2, sent=time.time() - 60)
    await _delete(555, [1])
    assert _reason(555, 1) == d.REASON_EXPIRED     # still classified
    assert armed == [None]                         # but announced


def test_quiet_reasons_default_on_and_read_raw_strings(env):
    fake, _ = env
    assert d.quiet_reasons() == {d.REASON_EXPIRED, d.REASON_WIPED}
    fake.d[d.CFG_QUIET_WIPES] = "false"            # `navig config set` stores a string
    assert d.quiet_reasons() == {d.REASON_EXPIRED}
    with pytest.raises(ValueError):
        d.set_quiet("nonsense", True)
