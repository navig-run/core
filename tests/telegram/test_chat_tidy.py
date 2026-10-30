"""Daily chat tidy: yesterday's routine bot messages are taken back overnight.

The ledger records ids only, only for private non-business chats, and — in the
default ``routine`` scope — only sends made inside a ``routine()`` block, so a
downloaded video or an AI answer is never swept.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from navig.telegram import chat_tidy

NOW = datetime(2026, 10, 3, 9, 0, 0)
OPERATOR = 4242


def _msg(mid: int, when: datetime, *, chat_type: str = "private", chat: int = OPERATOR, **extra):
    return {"message_id": mid, "date": int(when.timestamp()), "chat": {"id": chat, "type": chat_type}, **extra}


@pytest.fixture
def tidy(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    # system_* mirror the real settings() shape; system cards are OFF here so the
    # routine tests keep their historical semantics — each system test opts in.
    cfg = {
        "enabled": True,
        "hour": 4,
        "scope": "routine",
        "system_enabled": False,
        "system_hour": 4,
    }
    monkeypatch.setattr(chat_tidy, "settings", lambda fresh=False: cfg)
    monkeypatch.setattr(chat_tidy, "_last_attempt", 0.0)
    assert chat_tidy.ledger_path().parent.parent == tmp_path  # never the real ~/.navig
    # `mark_routine()` is meant for one-shot CLI processes and never resets, so a test
    # that runs `navig body checkin` through CliRunner leaves the marker set for every
    # later test on the same xdist worker — these tests then saw non-routine sends
    # recorded as routine. Reproduced with the pair
    # tests/regression/test_weigh_in_cadence.py + this file. Start each test unmarked.
    token = chat_tidy._ROUTINE.set(False)
    kind_token = chat_tidy._KIND.set("notice")
    yield cfg
    chat_tidy._KIND.reset(kind_token)
    chat_tidy._ROUTINE.reset(token)


class FakeApi:
    def __init__(self, ok=True):
        self.calls: list[tuple[str, dict]] = []
        self.ok = ok

    async def __call__(self, method, payload):
        self.calls.append((method, payload))
        return True if self.ok else None


def _ledger_ids():
    return sorted(e["m"] for e in chat_tidy.read_ledger(chat_tidy.ledger_path()))


# ── recording ───────────────────────────────────────────────────────────────


def test_disabled_records_nothing(tidy):
    tidy["enabled"] = False
    with chat_tidy.routine():
        chat_tidy.note(_msg(1, NOW))
    assert not chat_tidy.ledger_path().exists()


def test_routine_scope_skips_non_routine_sends(tidy):
    chat_tidy.note(_msg(1, NOW))  # an AI answer, a download: not routine
    with chat_tidy.routine():
        chat_tidy.note(_msg(2, NOW))
    chat_tidy.note(_msg(3, NOW))  # the block ended — back to not routine
    assert _ledger_ids() == [2]


def test_all_scope_records_every_private_send(tidy):
    tidy["scope"] = "all"
    chat_tidy.note(_msg(1, NOW))
    assert _ledger_ids() == [1]


def test_never_records_groups_or_business_chats(tidy):
    with chat_tidy.routine():
        chat_tidy.note(_msg(1, NOW, chat_type="supergroup", chat=-100))
        chat_tidy.note(_msg(2, NOW, business_connection_id="bc1"))
        chat_tidy.note(None)
        chat_tidy.note({"ok": True})
    assert not chat_tidy.ledger_path().exists()


def test_media_group_records_each_message(tidy):
    with chat_tidy.routine():
        chat_tidy.note([_msg(5, NOW), _msg(6, NOW)])
    assert _ledger_ids() == [5, 6]


def test_ledger_holds_no_text(tidy):
    with chat_tidy.routine():
        chat_tidy.note(_msg(1, NOW, text="Талия честнее весов"))
    raw = chat_tidy.ledger_path().read_text(encoding="utf-8")
    assert "Талия" not in raw
    entry = json.loads(raw.splitlines()[0])
    # ids, a timestamp, and the KIND label — a fixed vocabulary, never content.
    assert set(entry) == {"c", "m", "t", "k"}
    assert entry["k"] in chat_tidy.KINDS


def test_routine_marker_follows_awaits(tidy):
    async def send(mid):
        await asyncio.sleep(0)
        chat_tidy.note(_msg(mid, NOW))

    async def main():
        with chat_tidy.routine():
            await send(1)
        await send(2)

    asyncio.run(main())
    assert _ledger_ids() == [1]


# ── sweeping ────────────────────────────────────────────────────────────────


def _seed(*msgs):
    with chat_tidy.routine():
        for m in msgs:
            chat_tidy.note(m)


def test_sweep_deletes_yesterday_keeps_today(tidy):
    yesterday = NOW - timedelta(hours=14)  # 19:00 the evening before
    _seed(_msg(10, yesterday), _msg(11, yesterday), _msg(20, NOW - timedelta(hours=1)))
    api = FakeApi()

    res = asyncio.run(chat_tidy.sweep(api, now=NOW))

    assert api.calls == [("deleteMessages", {"chat_id": OPERATOR, "message_ids": [10, 11]})]
    assert (res.deleted, res.kept, res.failed) == (2, 1, 0)
    assert _ledger_ids() == [20]
    assert not chat_tidy.ledger_path().with_suffix(".sweeping").exists()


def test_sweep_drops_what_telegram_no_longer_lets_a_bot_delete(tidy):
    _seed(_msg(1, NOW - timedelta(hours=50)))
    api = FakeApi()
    res = asyncio.run(chat_tidy.sweep(api, now=NOW))
    assert api.calls == []
    assert res.expired == 1
    assert _ledger_ids() == []


def test_failed_delete_is_kept_for_retry(tidy):
    _seed(_msg(10, NOW - timedelta(hours=14)))
    res = asyncio.run(chat_tidy.sweep(FakeApi(ok=False), now=NOW))
    assert res.failed == 1
    assert _ledger_ids() == [10]


def test_sweep_batches_at_100(tidy):
    _seed(*[_msg(i, NOW - timedelta(hours=14)) for i in range(1, 251)])
    api = FakeApi()
    asyncio.run(chat_tidy.sweep(api, now=NOW))
    assert [len(p["message_ids"]) for _, p in api.calls] == [100, 100, 50]


def test_dry_run_changes_nothing(tidy):
    _seed(_msg(10, NOW - timedelta(hours=14)))
    api = FakeApi()
    res = asyncio.run(chat_tidy.sweep(api, now=NOW, dry_run=True))
    assert (res.deleted, api.calls) == (1, [])
    assert _ledger_ids() == [10]


def test_concurrent_sweep_is_refused(tidy):
    _seed(_msg(10, NOW - timedelta(hours=14)))
    with chat_tidy._lock() as held:
        assert held
        res = asyncio.run(chat_tidy.sweep(FakeApi(), now=NOW))
    assert res.busy
    assert _ledger_ids() == [10]


def test_leftover_from_a_crashed_sweep_is_not_lost(tidy):
    aside = chat_tidy.ledger_path().with_suffix(".sweeping")
    aside.parent.mkdir(parents=True, exist_ok=True)
    aside.write_text(json.dumps({"c": OPERATOR, "m": 7, "t": int((NOW - timedelta(hours=14)).timestamp())}) + "\n")
    api = FakeApi()
    asyncio.run(chat_tidy.sweep(api, now=NOW))
    assert api.calls == [("deleteMessages", {"chat_id": OPERATOR, "message_ids": [7]})]


def test_an_append_that_lands_in_the_taken_file_is_not_lost(tidy, monkeypatch):
    """POSIX: a send that opened the ledger just before the sweep renamed it writes
    into the renamed `.sweeping` file AFTER the sweep read it. Unlinking that file
    used to delete the message id, so the message was never tidied."""
    _seed(_msg(10, NOW - timedelta(hours=14)))
    real_take = chat_tidy._take
    late = {"c": OPERATOR, "m": 30, "t": int((NOW - timedelta(minutes=5)).timestamp()), "k": "notice"}

    def take_then_late_append():
        got = real_take()
        with open(chat_tidy.ledger_path().with_suffix(".sweeping"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(late) + "\n")
        return got

    monkeypatch.setattr(chat_tidy, "_take", take_then_late_append)
    asyncio.run(chat_tidy.sweep(FakeApi(), now=NOW))
    assert _ledger_ids() == [30]
    assert not chat_tidy.ledger_path().with_suffix(".sweeping").exists()


def test_an_unanswered_approval_card_is_protected_until_it_expires(tidy):
    """An approval card sent before midnight (or with a long timeout) still has live
    buttons the next morning; deleting it left request_approval() waiting on a
    prompt the operator could no longer tap."""
    import time

    from navig.approval import journal

    tidy["system_enabled"] = True
    yesterday = NOW - timedelta(hours=14)
    with chat_tidy.routine("system"):
        chat_tidy.note(_msg(10, yesterday))  # the approval card, still waiting
        chat_tidy.note(_msg(11, yesterday))  # another system card
    journal.record_pending("req-1", command="x", channel="mission", user_id="system",
                           level="CONFIRM", expires_at=time.time() + 900)
    journal.note_card("req-1", OPERATOR, 10)

    res = asyncio.run(chat_tidy.sweep(FakeApi(), now=NOW, dry_run=True))
    assert (res.protected, res.deleted) == (1, 1)

    journal.clear_pending("req-1")  # answered → swept like any other card
    res = asyncio.run(chat_tidy.sweep(FakeApi(), now=NOW, dry_run=True))
    assert (res.protected, res.deleted) == (0, 2)


# ── the once-a-day trigger ──────────────────────────────────────────────────


def test_maybe_sweep_waits_for_the_hour_then_runs_once(tidy):
    _seed(_msg(10, NOW - timedelta(hours=14)))
    api = FakeApi()

    assert asyncio.run(chat_tidy.maybe_sweep(api, now=NOW.replace(hour=3))) is None
    assert api.calls == []

    res = asyncio.run(chat_tidy.maybe_sweep(api, now=NOW))
    assert res is not None and res.deleted == 1
    assert chat_tidy.last_sweep_day() == "2026-10-03"

    chat_tidy._last_attempt = 0.0
    assert asyncio.run(chat_tidy.maybe_sweep(api, now=NOW + timedelta(hours=2))) is None
    assert len(api.calls) == 1


def test_maybe_sweep_off_when_disabled(tidy):
    tidy["enabled"] = False
    _seed(_msg(10, NOW - timedelta(hours=14)))
    assert asyncio.run(chat_tidy.maybe_sweep(FakeApi(), now=NOW)) is None


def test_failed_day_is_not_marked_done(tidy):
    _seed(_msg(10, NOW - timedelta(hours=14)))
    asyncio.run(chat_tidy.maybe_sweep(FakeApi(ok=False), now=NOW))
    assert chat_tidy.last_sweep_day() is None


# ── the send paths actually feed the ledger ─────────────────────────────────


def test_cli_send_path_records_routine(tidy, monkeypatch):
    from navig.commands import telegram as tg_cmd

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True, "result": _msg(33, NOW)}

    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp())
    tg_cmd._api_call("tok", "sendMessage", {"chat_id": OPERATOR, "text": "x"})
    assert not chat_tidy.ledger_path().exists()  # not routine: left alone
    with chat_tidy.routine():
        tg_cmd._api_call("tok", "sendMessage", {"chat_id": OPERATOR, "text": "x"})
        tg_cmd._api_call("tok", "editMessageText", {"chat_id": OPERATOR, "text": "x"})
    assert _ledger_ids() == [33]


def test_notifications_record_routine_but_never_alerts(tidy):
    from navig.gateway.notifications import Notification, TelegramNotifier

    mid = iter(range(100, 200))

    class Channel:
        async def send_message(self, chat_id, text, **kw):
            m = _msg(next(mid), NOW)
            chat_tidy.note(m)  # what TelegramChannel._api_call does on success
            return m

    notifier = TelegramNotifier.__new__(TelegramNotifier)
    notifier.channel = Channel()
    notifier.chat_id = OPERATOR
    notifier._should_suppress = lambda n: False

    asyncio.run(notifier._send_notification(Notification(type="routine", title="hi", message="m")))
    asyncio.run(notifier._send_notification(Notification(type="alert", title="down", message="m")))
    assert _ledger_ids() == [100]


# ── kinds: the operator chooses what tomorrow's sweep removes ────────────────


def _kinds():
    return [e["k"] for e in chat_tidy.read_ledger(chat_tidy.ledger_path())]


def test_a_send_is_recorded_with_its_kind(tidy):
    with chat_tidy.routine("greeting"):
        chat_tidy.note(_msg(1, NOW))
    with chat_tidy.routine("spark"):
        chat_tidy.note(_msg(2, NOW))
    chat_tidy.note(_msg(3, NOW), routine=True, kind="body")
    assert _kinds() == ["greeting", "spark", "body"]


def test_an_unknown_kind_is_recorded_as_notice(tidy):
    with chat_tidy.routine("made-up"):
        chat_tidy.note(_msg(1, NOW))
    assert _kinds() == ["notice"]


def test_a_line_from_before_kinds_reads_as_other(tidy):
    path = chat_tidy.ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"c": OPERATOR, "m": 9, "t": int(NOW.timestamp())}) + "\n",
                    encoding="utf-8")
    assert _kinds() == ["other"]
    assert chat_tidy.pending_by_kind() == {"other": 1}


def test_a_kept_kind_stays_in_the_chat(tidy):
    tidy["keep_kinds"] = frozenset({"greeting"})
    yesterday = NOW - timedelta(days=1)
    with chat_tidy.routine("greeting"):
        chat_tidy.note(_msg(1, yesterday))
    with chat_tidy.routine("spark"):
        chat_tidy.note(_msg(2, yesterday))
    calls: list = []

    async def api(method, payload):
        calls.append(payload["message_ids"])
        return True

    res = asyncio.run(chat_tidy.sweep(api, now=NOW))
    assert calls == [[2]]                    # the spark goes, the greeting stays
    assert res.deleted == 1 and res.spared == 1
    assert _kinds() == []                    # and the greeting is not retried tomorrow


def test_keeping_applies_when_due_not_when_sent(tidy):
    """Un-keeping a kind before the sweep still sweeps what was sent while kept."""
    yesterday = NOW - timedelta(days=1)
    with chat_tidy.routine("greeting"):
        chat_tidy.note(_msg(1, yesterday))
    tidy["keep_kinds"] = frozenset()

    async def api(method, payload):
        return True

    assert asyncio.run(chat_tidy.sweep(api, now=NOW)).deleted == 1


def test_todays_kept_kind_waits_like_everything_else(tidy):
    tidy["keep_kinds"] = frozenset({"greeting"})
    with chat_tidy.routine("greeting"):
        chat_tidy.note(_msg(1, NOW))

    async def api(method, payload):
        raise AssertionError("nothing is due")

    res = asyncio.run(chat_tidy.sweep(api, now=NOW))
    assert res.spared == 0 and _kinds() == ["greeting"]


def test_notify_types_map_to_kinds():
    assert chat_tidy.notify_kind("sale") == "sales"
    assert chat_tidy.notify_kind("deploy") == "deploy"
    assert chat_tidy.notify_kind("whatever") == "notice"


def test_deploy_receipts_are_routine_by_default():
    assert "deploy" in chat_tidy.DEFAULT_ROUTINE_NOTIFY_TYPES
    for alert in ("system_alert", "security_alert", "sales_check_failed"):
        assert alert not in chat_tidy.DEFAULT_ROUTINE_NOTIFY_TYPES


def test_the_notifier_never_records_alerts_or_heartbeats():
    from navig.gateway import notifications as n

    assert {"alert", "heartbeat"} <= n._NEVER_ROUTINE
    greet = n.Notification(type="routine", title="x", message="y",
                           metadata={"tidy_kind": "greeting"})
    assert n._tidy_kind(greet) == "greeting"
    assert n._tidy_kind(n.Notification(type="routine", title="Morning Briefing",
                                       message="")) == "briefing"
    assert n._tidy_kind(n.Notification(type="reminder", title="t", message="")) == "reminder"


# ── router sends: the producer can decide per message ────────────────────────


@pytest.mark.parametrize("type_key,priority,data,expected", [
    ("sale", "normal", None, "sales"),
    ("free_game", "normal", None, "games"),
    ("goal_digest", "normal", None, "digest"),
    ("deploy", "high", None, None),                       # "Deploy failed" stays
    ("free_game", "high", None, None),                    # "sign-in expired" stays
    ("connectivity", "normal", {"tidy": "status"}, "status"),   # "back online"
    ("connectivity", "high", None, None),                 # "brain offline"
    ("reminder", "normal", {"tidy": False}, None),        # a renewal still ahead
    ("system_alert", "normal", {"tidy": "bogus"}, None),  # unknown kind: no override
])
def test_notify_tidy_kind(tidy, type_key, priority, data, expected):
    assert chat_tidy.notify_tidy_kind(type_key, priority, data) == expected


def test_advance_warnings_are_not_tidied(monkeypatch):
    from navig.pim import reminders
    from navig.store import board

    class _Board:
        def reminder_links(self, ids):
            return {7: {"card_id": "c", "lead": "1d", "title": "Dentist"},
                    8: {"card_id": "c", "lead": "", "title": "Dentist"}}

    monkeypatch.setattr(board, "get_board_store", lambda: _Board())
    assert reminders.is_advance_warning(7) is True     # the day before: keep
    assert reminders.is_advance_warning(8) is False    # at the time: routine
    assert reminders.is_advance_warning(9) is False    # a /remindme: routine
    assert reminders.is_advance_warning(0) is False


# ── system cards: telegram.system_cleanup, ON by default ────────────────────
# Owner directive 2026-10-14: approval cards, config-health notices, the
# System Briefing, /status, /plans and OCR bubbles leave the chat the NEXT DAY,
# independently of the routine tidy, with an on/off switch.


def test_system_settings_default_to_on_in_a_fresh_config(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(chat_tidy, "_settings_cache", None)
    cfg = chat_tidy.settings(fresh=True)
    assert cfg["system_enabled"] is True
    assert cfg["system_hour"] == chat_tidy.DEFAULT_HOUR
    # the routine switch keeps its historical default: off
    assert cfg["enabled"] is False


def test_unreadable_config_turns_both_cleanups_off(tmp_path, monkeypatch):
    """Default-on is for a config that was READ with no key. An unreadable one may
    hold the operator's explicit off — sweeping then deletes cards they kept."""
    import navig.config

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(chat_tidy, "_settings_cache", None)
    monkeypatch.setattr(chat_tidy, "_last_attempt", 0.0)

    def _boom():
        raise OSError("config unreadable")

    monkeypatch.setattr(navig.config, "get_config_manager", _boom)
    cfg = chat_tidy.settings(fresh=True)
    assert cfg["system_enabled"] is False and cfg["enabled"] is False
    path = chat_tidy.ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"c": OPERATOR, "m": 1, "t": int((NOW - timedelta(hours=14)).timestamp()),
                                "k": "system"}) + "\n", encoding="utf-8")
    api = FakeApi()
    assert asyncio.run(chat_tidy.maybe_sweep(api, now=NOW)) is None
    assert api.calls == []
    monkeypatch.setattr(chat_tidy, "_settings_cache", None)


def test_system_cards_record_even_with_the_routine_tidy_off(tidy):
    tidy["enabled"] = False
    tidy["system_enabled"] = True
    with chat_tidy.routine("system"):
        chat_tidy.note(_msg(1, NOW))
    with chat_tidy.routine("greeting"):  # routine switch off → still not recorded
        chat_tidy.note(_msg(2, NOW))
    assert _ledger_ids() == [1]
    assert _kinds() == ["system"]


def test_system_cards_record_nothing_when_system_cleanup_is_off(tidy):
    tidy["enabled"] = True
    tidy["system_enabled"] = False
    with chat_tidy.routine("system"):
        chat_tidy.note(_msg(1, NOW))
    with chat_tidy.routine("greeting"):
        chat_tidy.note(_msg(2, NOW))
    assert _ledger_ids() == [2]  # routine on → routine still recorded


def test_conversation_is_never_recorded_in_system_mode(tidy):
    """The directive's floor: the operator's messages and the agent's replies
    are conversation. They are not routine sends, so with only the system
    switch on nothing outside `routine("system")` is ever ledgered."""
    tidy["enabled"] = False
    tidy["system_enabled"] = True
    chat_tidy.note(_msg(1, NOW))  # an AI answer (no routine marker)
    with chat_tidy.routine("greeting"):
        chat_tidy.note(_msg(2, NOW))
    assert not chat_tidy.ledger_path().exists()


def test_system_sweep_fires_on_its_own_hour_with_routine_off(tidy):
    tidy["enabled"] = False
    tidy["system_enabled"] = True
    tidy["system_hour"] = 5
    yesterday = NOW - timedelta(hours=14)
    with chat_tidy.routine("system"):
        chat_tidy.note(_msg(10, yesterday))
        chat_tidy.note(_msg(11, NOW - timedelta(hours=1)))  # today: kept
    api = FakeApi()

    # Before the system hour (and routine is off) → nothing is due.
    assert asyncio.run(chat_tidy.maybe_sweep(api, now=NOW.replace(hour=4))) is None
    assert api.calls == []

    res = asyncio.run(chat_tidy.maybe_sweep(api, now=NOW.replace(hour=6)))
    assert res is not None and res.deleted == 1
    assert api.calls == [("deleteMessages", {"chat_id": OPERATOR, "message_ids": [10]})]
    assert _ledger_ids() == [11]


def test_config_incidents_are_tagged_system_at_any_priority():
    assert chat_tidy.notify_tidy_kind("config_incident") == "system"
    assert chat_tidy.notify_tidy_kind("config_incident", "critical") == "system"
    # an explicit producer veto still wins over the system rule
    assert chat_tidy.notify_tidy_kind("config_incident", "high", {"tidy": False}) is None
    # and the historical rules are untouched
    assert chat_tidy.notify_tidy_kind("connectivity", "high") is None
    assert chat_tidy.notify_tidy_kind("sale", "normal") == "sales"


def test_the_system_kind_exists_and_is_named():
    assert "system" in chat_tidy.KINDS
    assert "System NAVIG cards" in chat_tidy.KINDS["system"]


def _seed_both(yesterday):
    with chat_tidy.routine("system"):
        chat_tidy.note(_msg(10, yesterday))
    with chat_tidy.routine("greeting"):
        chat_tidy.note(_msg(11, yesterday))


def test_each_schedule_sweeps_its_own_kinds_at_its_own_hour(tidy):
    """Regression: with BOTH switches on and different hours, each schedule sweeps
    only its own kinds at its own hour. The old OR gate swept routine cards at the
    system hour; the max() gate that replaced it held system cards until the
    routine hour (hours 23/4 → system cards waited 19 h, past the delete window)."""
    tidy["enabled"] = True
    tidy["hour"] = 6
    tidy["system_enabled"] = True
    tidy["system_hour"] = 4
    _seed_both(NOW - timedelta(hours=14))
    api = FakeApi()

    # 05:00 — the system hour passed, daily's 06:00 has not → only system cards go.
    res = asyncio.run(chat_tidy.maybe_sweep(api, now=NOW.replace(hour=5)))
    assert res is not None and res.deleted == 1
    assert api.calls == [("deleteMessages", {"chat_id": OPERATOR, "message_ids": [10]})]
    assert _ledger_ids() == [11]
    # …and the system schedule does not fire twice the same day.
    assert asyncio.run(chat_tidy.maybe_sweep(api, now=NOW.replace(hour=5, minute=30))) is None

    # 06:00 — the routine hour → the routine card goes, in its own sweep.
    res = asyncio.run(chat_tidy.maybe_sweep(api, now=NOW.replace(hour=6)))
    assert res is not None and res.deleted == 1
    assert api.calls[1:] == [("deleteMessages", {"chat_id": OPERATOR, "message_ids": [11]})]
    assert _ledger_ids() == []


def test_a_late_routine_hour_never_delays_system_cards(tidy):
    tidy["enabled"] = True
    tidy["hour"] = 23
    tidy["system_enabled"] = True
    tidy["system_hour"] = 4
    _seed_both(NOW - timedelta(hours=14))
    api = FakeApi()
    asyncio.run(chat_tidy.maybe_sweep(api, now=NOW.replace(hour=5)))
    assert api.calls == [("deleteMessages", {"chat_id": OPERATOR, "message_ids": [10]})]
    assert _ledger_ids() == [11]


def test_a_routine_sweep_leaves_cards_of_a_switched_off_system_cleanup(tidy):
    """Cards recorded while system cleanup was on are not the routine tidy's to delete
    once the operator switches system cleanup off."""
    tidy["system_enabled"] = True
    _seed_both(NOW - timedelta(hours=14))
    tidy["system_enabled"] = False
    api = FakeApi()
    res = asyncio.run(chat_tidy.maybe_sweep(api, now=NOW))
    assert res is not None and res.deleted == 1
    assert api.calls == [("deleteMessages", {"chat_id": OPERATOR, "message_ids": [11]})]
    assert _ledger_ids() == [10]


def test_a_legacy_state_file_counts_for_both_schedules(tidy):
    chat_tidy._state_path().parent.mkdir(parents=True, exist_ok=True)
    chat_tidy._state_path().write_text(json.dumps({"last_day": "2026-10-03"}), encoding="utf-8")
    assert chat_tidy.last_sweep_day("routine") == "2026-10-03"
    assert chat_tidy.last_sweep_day("system") == "2026-10-03"
    chat_tidy.mark_swept("2026-10-04", ("system",))
    assert chat_tidy.last_sweep_day("routine") == "2026-10-03"
    assert chat_tidy.last_sweep_day("system") == "2026-10-04"
    assert chat_tidy.last_sweep_day() == "2026-10-04"


def test_no_enabled_switch_means_no_sweep(tidy):
    tidy["enabled"] = False
    tidy["system_enabled"] = False
    _seed(_msg(10, NOW - timedelta(hours=14)))
    assert asyncio.run(chat_tidy.maybe_sweep(FakeApi(), now=NOW)) is None
