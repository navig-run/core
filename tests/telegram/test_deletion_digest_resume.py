"""A digest pending at restart is sent, and sent ONCE across both processes.

Two gaps found after #1564, while bringing the digest live:

* ``arm_digest`` was only called when a deletion ARRIVED, so a window pending when
  the process stopped sat unsent until the next deletion — hours on a quiet
  account. The operator restarts often (five times in two days, measured).
* Resuming at startup runs in BOTH supervisor processes (gateway + telegram_worker,
  each with its own channel) — the exact shape that doubled every boot greeting. So
  the flush claims its window cross-process before sending.
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
    return fake


def _ch():
    ch = MagicMock()
    ch.bot_token = "8490556839:ABC"
    ch._api_call = AsyncMock(return_value={"message_id": 1})
    return ch


def _store():
    from navig.store.telegram_catalog import TelegramCatalogStore
    return TelegramCatalogStore()


async def _delete(ch, chat_id, mid):
    _store().upsert_message(chat_id, mid, sender_id=777, sender_name="operator",
                            date="1", text="hi", kind="business", raw={"business": True})
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": [mid], "chat": {"id": chat_id}})


def _batch_key():
    """The claim key production uses: the pending batch's newest deleted_at — the
    one thing two processes see identically."""
    return _store().count_deleted_since(d._watermark())["latest"]


def _cards(ch):
    return [c.args[1] for c in ch._api_call.call_args_list
            if c.args and c.args[0] == "sendMessage"]


# ── resume ────────────────────────────────────────────────────────────────────


async def test_a_window_pending_at_restart_is_re_armed(env, monkeypatch):
    armed: list = []
    monkeypatch.setattr(d, "arm_digest", lambda ch, delay=None: armed.append(delay))
    await _delete(_ch(), 555, 1)
    armed.clear()                       # the arrival itself armed one — "restart" now

    res = await d.resume_pending(_ch())
    assert res == {"armed": True, "pending": 1, "chats": 1}
    # A SHORT delay, not a full window: these already waited before the restart.
    assert armed == [d.RESUME_DELAY_SEC]


async def test_resume_with_nothing_pending_arms_nothing(env, monkeypatch):
    armed: list = []
    monkeypatch.setattr(d, "arm_digest", lambda ch, delay=None: armed.append(delay))
    assert (await d.resume_pending(_ch()))["reason"] == "nothing_pending"
    assert armed == []


@pytest.mark.parametrize("setup, reason", [
    (lambda env: env.d.__setitem__(d.CFG_MODE, "instant"), "mode=instant"),
    (lambda env: env.d.__setitem__(d.CFG_MODE, "off"), "mode=off"),
    (lambda env: env.d.__setitem__(d.CFG_RECORD, False), "recording off"),
])
async def test_resume_respects_every_switch(env, monkeypatch, setup, reason):
    await _delete(_ch(), 555, 1)
    setup(env)
    armed: list = []
    monkeypatch.setattr(d, "arm_digest", lambda ch, delay=None: armed.append(delay))
    assert (await d.resume_pending(_ch()))["reason"] == reason
    assert armed == []


async def test_resume_does_nothing_when_the_business_inbox_is_off(env, monkeypatch):
    await _delete(_ch(), 555, 1)
    monkeypatch.setattr(b.permissions, "business_enabled", lambda: False)
    assert (await d.resume_pending(_ch()))["reason"] == "business inbox off"


async def test_deletions_from_before_deleted_at_existed_are_not_resent(env, monkeypatch):
    """The first boot on this code meets rows the OLD code marked deleted — and
    already announced instantly — with deleted_at NULL. Resuming must not re-send
    them as a fresh digest."""
    _store().upsert_message(555, 1, sender_id=777, sender_name="s", date="1", text="old",
                            kind="business", raw={"business": True})
    _store()._write("UPDATE tg_messages SET deleted = 1, deleted_at = NULL "
                    "WHERE chat_id = 555 AND message_id = 1")
    armed: list = []
    monkeypatch.setattr(d, "arm_digest", lambda ch, delay=None: armed.append(delay))
    assert (await d.resume_pending(_ch()))["reason"] == "nothing_pending"
    assert armed == []


# ── the cross-process claim ───────────────────────────────────────────────────


async def test_two_processes_flushing_one_window_send_one_card(env):
    """The gateway and the telegram_worker both resume at boot, in the same second."""
    first, second = _ch(), _ch()
    await _delete(first, 555, 1)

    # The sibling got there first. Both see the SAME pending rows, so both derive
    # the same key — which is why the key is the batch, not a clock reading.
    assert d._claim_window(_batch_key()) is True
    res = await d.flush_digest(second)
    assert res["sent"] is False and res["reason"] == "claimed_by_sibling"
    assert _cards(second) == []


async def test_a_failed_send_hands_the_window_back(env):
    """A rejected card that kept its claim would leave the window unreported until
    the TTL — or forever, if nothing else tried."""
    ch = _ch()
    await _delete(ch, 555, 1)
    key = _batch_key()
    ch._api_call = AsyncMock(return_value=None)
    assert (await d.flush_digest(ch))["sent"] is False
    assert not d._claim_path(key).exists()          # released
    ch._api_call = AsyncMock(return_value={"message_id": 2})
    assert (await d.flush_digest(ch))["sent"] is True


def test_a_stale_claim_from_a_dead_sender_is_taken_over(env):
    since = d._watermark()
    assert d._claim_window(since, now=1000.0) is True
    assert d._claim_window(since, now=1000.0 + d.CLAIM_TTL_SEC - 1) is False
    assert d._claim_window(since, now=1000.0 + d.CLAIM_TTL_SEC + 1) is True


def test_an_unreadable_claim_is_stale_not_permanent(env):
    since = d._watermark()
    path = d._claim_path(since)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("garbage", encoding="utf-8")
    assert d._claim_window(since) is True


async def test_declining_to_send_does_not_take_the_claim(env, monkeypatch):
    """Claimed only AFTER every "don't send" check: a claim taken for a window this
    process then declines (quiet hours) would stop the sibling from sending it."""
    ch = _ch()
    await _delete(ch, 555, 1)
    key = _batch_key()
    monkeypatch.setattr(d, "should_notify", lambda: (False, "quiet hours"))
    assert (await d.flush_digest(ch))["reason"] == "quiet hours"
    assert not d._claim_path(key).exists()


def test_old_claims_are_pruned(env):
    import os

    folder = d._claim_path(d._watermark()).parent
    folder.mkdir(parents=True, exist_ok=True)
    old, fresh = folder / "1.claim", folder / "2.claim"
    old.write_text("1", encoding="utf-8")
    fresh.write_text("2", encoding="utf-8")
    os.utime(old, (1000, 1000))                      # long ago
    assert d._prune_claims(now=1000 + 90_000) == 1
    assert not old.exists() and fresh.exists()


async def test_a_delivered_card_prunes_as_it_goes(env):
    """One claim file per sent card would otherwise accumulate forever."""
    import os

    folder = d._claim_path(d._watermark()).parent
    folder.mkdir(parents=True, exist_ok=True)
    stale = folder / "9.claim"
    stale.write_text("9", encoding="utf-8")
    os.utime(stale, (1000, 1000))
    ch = _ch()
    await _delete(ch, 555, 1)
    assert (await d.flush_digest(ch))["sent"] is True
    assert not stale.exists()


def test_the_channel_resumes_the_digest_at_start():
    """Source contract: channel start must call resume_pending. Without this the
    next refactor of the start path silently reinstates the restart gap."""
    import inspect

    import navig.gateway.channels.telegram as tg

    src = inspect.getsource(tg)
    assert "deletions.resume_pending(self)" in src


async def test_two_processes_agree_on_the_key_even_with_no_watermark_stored(env):
    """The bug the race test caught: with no watermark stored, each process computes
    since = now - window itself, milliseconds apart. A since-keyed claim then gave
    the two processes DIFFERENT keys and both sent. Keyed on the batch, two
    independent reads — as two processes would make — must agree."""
    await _delete(_ch(), 555, 1)
    assert d.CFG_WATERMARK not in env.d            # the first-run path
    first_since, second_since = d._watermark(), d._watermark()
    first = _store().count_deleted_since(first_since)["latest"]
    second = _store().count_deleted_since(second_since)["latest"]
    assert first and first == second
    assert d._claim_window(first) is True
    assert d._claim_window(second) is False        # the sibling stands down
