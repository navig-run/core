"""The scheduled weigh-in must not ask twice a day, nor when it has nothing to ask.

Operator report: *"The bot asks my weight every day, and again after restarts
sometimes. I want it less frequent."* Traced to three things, each pinned here:

* the scheduler catches up a missed 08:10 run the moment the daemon comes back
  (13:35, 13:06 in the log) — so the command itself refuses after
  ``health.weigh_in.latest_hour``;
* a cold start ran 64 s against a 60 s timeout, was marked failed after sending,
  and the retry asked again — so the "asked" mark is written BEFORE the send and
  a second ``--send`` the same day skips;
* ``/weigh 87.4`` and ``navig body log --weight`` recorded the weight but left
  the morning's ``force_reply`` prompt armed — so both now clear it.

And the option asked for: ``navig body cadence`` (every N days, weekdays, latest
hour, on/off). Hermetic: config, data and the prompt-state file live under
tmp_path; Telegram is a recording fake; the clock is pinned through ``body._now``.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from typer.testing import CliRunner

from navig.commands import body as body_cmd
from navig.spaces import body_metrics as bm
from navig.spaces import weigh_cadence as wc

runner = CliRunner()
HEADER = "date,weight_kg,body_fat_pct,resting_hr,sleep_hours,steps,mood_1_10,hrv,notes\n"
CHAT = 424242
#: A Thursday morning, 08:10 — the cron's own slot.
MORNING = datetime(2026, 10, 8, 8, 10)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    yield tmp_path
    reset_config_manager()
    ConfigSingleton._instance = None


@pytest.fixture
def space(tmp_path):
    (tmp_path / "metrics.csv").write_text(HEADER, encoding="utf-8")
    return tmp_path


@pytest.fixture
def telegram(monkeypatch):
    """Recording stand-in for the CLI's Telegram calls. Never touches the network."""
    calls: list[tuple[str, dict]] = []
    state = {"fail": False, "next_id": 100}

    def fake_api_call(token, method, payload):
        calls.append((method, payload))
        if state["fail"]:
            raise RuntimeError("network down")
        state["next_id"] += 1
        return {"ok": True, "result": {"message_id": state["next_id"]}}

    import navig.gateway.channels.telegram_extensions as ext
    import navig.telegram.api as tg_cmd

    monkeypatch.setattr(tg_cmd, "_api_call", fake_api_call)
    monkeypatch.setattr(tg_cmd, "_load_telegram_token", lambda: "TOKEN")
    monkeypatch.setattr(ext, "is_enabled", lambda _id: True)
    monkeypatch.setattr(body_cmd, "_default_chat_id", lambda: CHAT)
    return {"calls": calls, "state": state}


def _at(monkeypatch, when: datetime) -> None:
    monkeypatch.setattr(body_cmd, "_now", lambda: when)


def _send(space, *extra):
    return runner.invoke(
        body_cmd.body_app, ["checkin", "--send", "--json", "--space", str(space), *extra]
    )


def _payload(result) -> dict:
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _sends(telegram) -> list[dict]:
    return [p for m, p in telegram["calls"] if m == "sendMessage"]


def _set(**values) -> None:
    from navig.config import get_config_manager

    cm = get_config_manager()
    for key, value in values.items():
        cm.set_global(f"{wc.CONFIG_KEY}.{key}", value)


# ── never twice a day ─────────────────────────────────────────────────────────


def test_the_first_morning_run_asks(space, telegram, monkeypatch):
    _at(monkeypatch, MORNING)
    out = _payload(_send(space))
    assert out["sent"] is True
    assert len(_sends(telegram)) == 1
    assert bm.weigh_asked_on(CHAT) == "2026-10-08"


def test_a_retry_after_a_timed_out_run_does_not_ask_again(space, telegram, monkeypatch):
    """The 64 s-vs-60 s case: the first run sent, the scheduler retried."""
    _at(monkeypatch, MORNING)
    _payload(_send(space))
    _at(monkeypatch, datetime(2026, 10, 8, 8, 16))
    out = _payload(_send(space))
    assert out == {**out, "sent": False, "skipped": "asked"}
    assert len(_sends(telegram)) == 1


def test_the_mark_survives_the_answer(space, telegram, monkeypatch):
    """Answering clears the pending prompt; a restart afterwards must still not re-ask."""
    _at(monkeypatch, MORNING)
    _payload(_send(space))
    bm.clear_prompt(CHAT)  # what consume_reply does on an answer
    _at(monkeypatch, datetime(2026, 10, 8, 9, 30))
    assert _payload(_send(space))["skipped"] == "asked"
    assert len(_sends(telegram)) == 1


def test_a_send_that_failed_does_not_count_as_asked(space, telegram, monkeypatch):
    _at(monkeypatch, MORNING)
    telegram["state"]["fail"] = True
    result = _send(space)
    assert result.exit_code == 1
    assert bm.weigh_asked_on(CHAT) is None
    telegram["state"]["fail"] = False
    assert _payload(_send(space))["sent"] is True


def test_the_next_day_asks_again(space, telegram, monkeypatch):
    _at(monkeypatch, MORNING)
    _payload(_send(space))
    _at(monkeypatch, datetime(2026, 10, 9, 8, 10))
    assert _payload(_send(space))["sent"] is True
    assert len(_sends(telegram)) == 2


def test_force_sends_anyway(space, telegram, monkeypatch):
    _at(monkeypatch, MORNING)
    _payload(_send(space))
    assert _payload(_send(space, "--force"))["sent"] is True
    assert len(_sends(telegram)) == 2


# ── already weighed ───────────────────────────────────────────────────────────


def test_already_weighed_today_skips(space, telegram, monkeypatch):
    _at(monkeypatch, MORNING)
    bm.upsert(space / "metrics.csv", "2026-10-08", {"weight_kg": "87.4"})
    out = _payload(_send(space))
    assert out["skipped"] == "weighed"
    assert _sends(telegram) == []


def test_weighed_yesterday_still_asks_on_the_daily_default(space, telegram, monkeypatch):
    _at(monkeypatch, MORNING)
    bm.upsert(space / "metrics.csv", "2026-10-07", {"weight_kg": "87.4"})
    assert _payload(_send(space))["sent"] is True


# ── every N days ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("last", "expected"),
    [("2026-10-07", "weighed"), ("2026-10-06", "weighed"), ("2026-10-05", None)],
)
def test_every_three_days_is_by_date(space, telegram, monkeypatch, last, expected):
    _set(every_days=3)
    _at(monkeypatch, MORNING)
    bm.upsert(space / "metrics.csv", last, {"weight_kg": "87.4"})
    out = _payload(_send(space))
    if expected is None:
        assert out["sent"] is True
    else:
        assert out["skipped"] == expected


def test_every_days_read_from_a_config_set_string(space, telegram, monkeypatch):
    """`navig config set health.weigh_in.every_days 3` stores the STRING "3"."""
    _set(every_days="3")
    _at(monkeypatch, MORNING)
    bm.upsert(space / "metrics.csv", "2026-10-07", {"weight_kg": "87.4"})
    assert _payload(_send(space))["skipped"] == "weighed"


# ── weekdays ──────────────────────────────────────────────────────────────────


def test_weekdays_filter(space, telegram, monkeypatch):
    _set(weekdays=["mon", "thu"])
    _at(monkeypatch, MORNING)  # Thursday
    assert _payload(_send(space))["sent"] is True
    _at(monkeypatch, datetime(2026, 10, 9, 8, 10))  # Friday
    assert _payload(_send(space))["skipped"] == "weekday"


# ── too late today ────────────────────────────────────────────────────────────


def test_a_catch_up_run_after_the_latest_hour_skips(space, telegram, monkeypatch):
    """The daemon came back at 13:35 with a noon cutoff set."""
    _set(latest_hour=12)
    _at(monkeypatch, datetime(2026, 10, 8, 13, 35))
    out = _payload(_send(space))
    assert out["skipped"] == "late"
    assert _sends(telegram) == []


def test_a_catch_up_run_before_the_latest_hour_asks(space, telegram, monkeypatch):
    _set(latest_hour=12)
    _at(monkeypatch, datetime(2026, 10, 8, 11, 59))
    assert _payload(_send(space))["sent"] is True


def test_latest_hour_24_never_cuts_off(space, telegram, monkeypatch):
    _set(latest_hour=24)
    _at(monkeypatch, datetime(2026, 10, 8, 23, 30))
    assert _payload(_send(space))["sent"] is True


def test_no_cutoff_by_default(space, telegram, monkeypatch):
    """An afternoon-scheduled weigh-in must keep working without any setting."""
    _at(monkeypatch, datetime(2026, 10, 8, 18, 0))
    assert _payload(_send(space))["sent"] is True


# ── off ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [False, "false", "off", "0"])
def test_enabled_false_skips(space, telegram, monkeypatch, value):
    _set(enabled=value)
    _at(monkeypatch, MORNING)
    assert _payload(_send(space))["skipped"] == "off"
    assert _sends(telegram) == []


def test_the_weekly_card_is_not_subject_to_the_cadence(space, telegram, monkeypatch):
    _set(enabled=False)
    _at(monkeypatch, datetime(2026, 10, 11, 18, 45))
    out = _payload(_send(space, "--weekly"))
    assert out["sent"] is True and out["kind"] == "weekly"


# ── a weight logged elsewhere clears the pending prompt ───────────────────────


def test_body_log_weight_clears_and_deletes_the_pending_prompt(space, telegram, monkeypatch):
    _at(monkeypatch, MORNING)
    _payload(_send(space))
    _mid = bm.pending_prompt(CHAT)[2]
    result = runner.invoke(
        body_cmd.body_app,
        ["log", "--weight", "87.4", "--json", "--space", str(space)],
    )
    out = _payload(result)
    assert out["prompts_cleared"] == 1
    assert bm.pending_prompt(CHAT) is None
    assert ("deleteMessage", {"chat_id": CHAT, "message_id": _mid}) in telegram["calls"]


def test_body_log_for_another_day_leaves_todays_prompt(space, telegram, monkeypatch):
    _at(monkeypatch, MORNING)
    _payload(_send(space))
    result = runner.invoke(
        body_cmd.body_app,
        ["log", "--weight", "87.4", "--date", "2026-10-01", "--json", "--space", str(space)],
    )
    assert _payload(result)["prompts_cleared"] == 0
    assert bm.pending_prompt(CHAT) is not None


async def test_slash_weigh_with_a_value_clears_and_deletes_the_pending_prompt(tmp_path):
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    path = tmp_path / "metrics.csv"
    path.write_text(HEADER, encoding="utf-8")
    bm.remember_target(CHAT, path)
    bm.set_prompt(CHAT, "weigh", date.today().isoformat(), 777)

    class _Chan:
        def __init__(self):
            self.calls: list[tuple[str, dict]] = []
            self.sent: list[str] = []

        async def _api_call(self, method, payload):
            self.calls.append((method, payload))
            return {"message_id": 1}

        async def send_message(self, chat_id, text, **_):
            self.sent.append(text)
            return {"message_id": 2}

    chan = _Chan()
    await TelegramCommandsMixin._handle_weigh(chan, CHAT, CHAT, "/weigh 87")
    assert bm.pending_prompt(CHAT) is None
    assert ("deleteMessage", {"chat_id": CHAT, "message_id": 777}) in chan.calls
    assert chan.sent and chan.sent[-1].startswith("✅")
    assert bm.latest(path)[1] == 87.0


async def test_slash_weigh_without_a_pending_prompt_deletes_nothing(tmp_path):
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    path = tmp_path / "metrics.csv"
    path.write_text(HEADER, encoding="utf-8")
    bm.remember_target(CHAT, path)

    class _Chan:
        calls: list = []

        async def _api_call(self, method, payload):
            self.calls.append(method)
            return {}

        async def send_message(self, chat_id, text, **_):
            return {}

    chan = _Chan()
    await TelegramCommandsMixin._handle_weigh(chan, CHAT, CHAT, "/weigh 87")
    assert "deleteMessage" not in chan.calls


# ── navig body cadence ────────────────────────────────────────────────────────


def _cadence(space, *args):
    return runner.invoke(body_cmd.body_app, ["cadence", *args, "--json", "--space", str(space)])


def test_cadence_shows_the_defaults(space, telegram, monkeypatch):
    _at(monkeypatch, MORNING)
    out = _payload(_cadence(space))
    assert (out["enabled"], out["every_days"], out["weekdays"], out["latest_hour"]) == (
        True,
        1,
        [],
        24,
    )
    assert out["next_eligible"] == "2026-10-08"


def test_cadence_set_persists_and_drives_next_eligible(space, telegram, monkeypatch):
    _at(monkeypatch, MORNING)
    bm.upsert(space / "metrics.csv", "2026-10-07", {"weight_kg": "87.4"})
    out = _payload(_cadence(space, "--every", "3", "--days", "mon,thu", "--latest-hour", "12"))
    assert out["every_days"] == 3
    assert out["weekdays"] == ["mon", "thu"]
    assert out["latest_hour"] == 12
    # 10-07 + 3 = 10-10 (Sat) → first Mon/Thu on or after is Mon 10-12.
    assert out["next_eligible"] == "2026-10-12"
    # And a fresh read (what the next cron run does) sees the same.
    from navig.config import reset_config_manager

    reset_config_manager()
    assert wc.load_settings().every_days == 3


def test_cadence_off_and_on(space, telegram, monkeypatch):
    _at(monkeypatch, MORNING)
    assert _payload(_cadence(space, "--off"))["enabled"] is False
    assert _payload(_cadence(space))["next_eligible"] is None
    assert _payload(_cadence(space, "--on"))["enabled"] is True


def test_cadence_days_any_clears_the_filter(space, telegram, monkeypatch):
    _at(monkeypatch, MORNING)
    _payload(_cadence(space, "--days", "mon"))
    assert _payload(_cadence(space, "--days", "any"))["weekdays"] == []


@pytest.mark.parametrize(
    "args",
    [
        ["--every", "0"],
        ["--every", "31"],
        ["--days", "mon,funday"],
        ["--latest-hour", "0"],
        ["--latest-hour", "25"],
    ],
)
def test_cadence_rejects_bad_input_with_a_non_zero_exit(space, telegram, monkeypatch, args):
    _at(monkeypatch, MORNING)
    result = _cadence(space, *args)
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False
    # Nothing was written.
    assert wc.load_settings() == wc.WeighInSettings()


def test_cadence_table_renders(space, telegram, monkeypatch):
    _at(monkeypatch, MORNING)
    result = runner.invoke(body_cmd.body_app, ["cadence", "--space", str(space)])
    assert result.exit_code == 0, result.output
    assert "Weigh-in cadence" in result.output
    assert "Next eligible" in result.output


# ── the pure rules ────────────────────────────────────────────────────────────


def test_malformed_config_falls_back_to_defaults():
    s = wc.settings_from(
        {"every_days": "lots", "latest_hour": 99, "weekdays": "someday", "enabled": "maybe"}
    )
    assert s == wc.WeighInSettings()


def test_skip_reason_order_off_wins():
    s = wc.WeighInSettings(enabled=False)
    assert (
        wc.skip_reason(s, day=MORNING.date(), now=MORNING, last_weighed=None, asked_on=None)[0]
        == "off"
    )
