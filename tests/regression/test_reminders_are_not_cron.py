"""A one-off reminder is a todo with ``--at``, never a date-pinned cron job.

The incident: an agent scheduled one-off Telegram reminders as cron jobs
(``0 11 9 10 *`` → "9 October 11:00"). Cron has no year field, so every one of them
came back with a next run a YEAR later, and none of them showed anywhere a reminder
should (todo list, Deck). Two fixes are pinned here:

* ``navig cron add`` (and both gateway routes) refuse a schedule pinned to one
  calendar date unless ``--yearly`` / ``yearly: true`` says that is the point;
* ``navig todo add --at`` takes the moment explicitly and keeps the text verbatim —
  because the sentence parser reads a date out of the TEXT, and reminder text mentions
  other dates ("bring the scan from 30/09" became due on 30/09 of the next year).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from navig.pim.dates import parse_at
from navig.scheduler.cron_service import CronParser

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 10, 9, 16, 0, tzinfo=TZ)


# ── parse_at ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("2026-10-12T09:00", datetime(2026, 10, 12, 9, 0, tzinfo=TZ)),
        ("2026-10-12 09:30", datetime(2026, 10, 12, 9, 30, tzinfo=TZ)),
        # a bare date is the morning, like split_due — never a midnight ping
        ("2026-10-12", datetime(2026, 10, 12, 9, 0, tzinfo=TZ)),
    ],
)
def test_parse_at_reads_iso_in_the_operators_zone(phrase, expected):
    assert parse_at(phrase, NOW) == expected


def test_parse_at_falls_back_to_short_phrases():
    when = parse_at("tomorrow 9am", NOW)
    assert when == datetime(2026, 10, 10, 9, 0, tzinfo=TZ)


@pytest.mark.parametrize("phrase", ["", "   ", "whenever you like", "2026-13-45"])
def test_parse_at_returns_none_for_what_it_cannot_read(phrase):
    assert parse_at(phrase, NOW) is None


# ── CronParser.yearly_pin ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "schedule,pin",
    [
        ("0 11 9 10 *", (9, 10)),  # the incident: oliviers:relance-rappel
        ("0 18 30 9 *", (30, 9)),
        ("0 9 7 10 *", (7, 10)),
        ("0 9 7 10 1", (7, 10)),  # a weekday on top does not make it recur more
    ],
)
def test_a_literal_day_and_month_is_a_yearly_pin(schedule, pin):
    assert CronParser.yearly_pin(schedule) == pin
    assert "navig todo add" in (CronParser.yearly_pin_error(schedule) or "")


@pytest.mark.parametrize(
    "schedule",
    [
        "0 9 * * *",
        "0 9 * * 1-5",
        "0 8 1 * *",  # monthly
        "0 9 1 1,4,7,10 *",  # quarterly
        "0 9 * 10 *",  # every day in October
        "0 9 1-7 10 *",
        "every 30 minutes",
        "daily at 9am",
        "",
    ],
)
def test_genuinely_recurring_schedules_are_not_pinned(schedule):
    assert CronParser.yearly_pin(schedule) is None
    assert CronParser.yearly_pin_error(schedule) is None


# ── navig todo add --at ─────────────────────────────────────────────────────


class _FakeStore:
    def __init__(self):
        self.created: list[dict] = []

    def create_todo(self, title, **fields):
        row = {"id": "t1", "title": title, **fields}
        self.created.append(row)
        return row


@pytest.fixture
def todo_cli(monkeypatch):
    from navig.commands import todo

    store = _FakeStore()
    monkeypatch.setattr(todo, "_store", lambda: store)
    monkeypatch.setattr(todo, "_now", lambda: NOW)
    monkeypatch.setattr(todo, "_sync_reminders", lambda _todo, _now: 1)
    monkeypatch.setattr(todo, "_report_reminders", lambda *a, **k: None)
    monkeypatch.setattr(todo, "_banner", lambda: None)
    return todo.todo_app, store


def test_todo_add_at_keeps_the_text_verbatim_and_uses_the_exact_time(todo_cli):
    app, store = todo_cli
    text = "RDV 11h45 - apporter l IRM du 30/09"
    result = CliRunner().invoke(app, ["add", text, "--at", "2026-10-12 09:00", "-s", "health"])
    assert result.exit_code == 0, result.output
    (row,) = store.created
    # Without --at the parser took "30/09" as the due date AND cut it out of the text.
    assert row["title"] == text
    assert row["due_at"] == "2026-10-12T07:00:00.000Z"  # 09:00 at UTC+2
    assert row["space"] == "health"
    assert row["recur"] is None


def test_todo_add_at_in_the_past_is_refused_not_stored(todo_cli):
    app, store = todo_cli
    result = CliRunner().invoke(app, ["add", "too late", "--at", "2026-10-09 11:00"])
    assert result.exit_code == 1
    assert store.created == []


def test_todo_add_at_unreadable_is_refused_not_stored(todo_cli):
    app, store = todo_cli
    result = CliRunner().invoke(app, ["add", "something", "--at", "whenever"])
    assert result.exit_code == 1
    assert store.created == []


# ── navig cron add ──────────────────────────────────────────────────────────


@pytest.fixture
def cron_cli(monkeypatch):
    from navig.commands import cron

    calls: list[dict] = []

    def fake_api(method, path, *, action, json_body=None, **_):
        calls.append(json_body or {})
        return {"id": "job_x", "next_run": "soon"}

    monkeypatch.setattr(cron, "_cron_api", fake_api)
    return cron.cron_app, calls


def test_cron_add_refuses_a_date_pinned_schedule(cron_cli):
    app, calls = cron_cli
    result = CliRunner().invoke(
        app, ["add", "rappel", "0 11 9 10 *", "navig telegram send 1 -m hi"]
    )
    assert result.exit_code == 2
    assert calls == []  # nothing reached the gateway


def test_cron_add_yearly_lets_it_through_and_says_so_to_the_gateway(cron_cli):
    app, calls = cron_cli
    result = CliRunner().invoke(
        app, ["add", "birthday", "0 9 7 10 *", "navig telegram send 1 -m hi", "--yearly"]
    )
    assert result.exit_code == 0, result.output
    assert calls and calls[0]["yearly"] is True


def test_cron_add_recurring_schedule_is_unaffected(cron_cli):
    app, calls = cron_cli
    result = CliRunner().invoke(app, ["add", "daily", "0 9 * * *", "navig backup export"])
    assert result.exit_code == 0, result.output
    assert "yearly" not in calls[0]


# ── the Deck route's shared validator ───────────────────────────────────────


def test_deck_schedule_validator_refuses_a_pin_unless_yearly():
    from navig.gateway.deck.routes.schedule import _schedule_error

    assert "navig todo add" in (_schedule_error("0 11 9 10 *") or "")
    assert _schedule_error("0 11 9 10 *", yearly=True) is None
    # `config set`-style string false must not wave it through
    assert _schedule_error("0 11 9 10 *", yearly="false") is not None
    assert _schedule_error("0 9 * * *") is None


# ── the interactive-menu wrapper ────────────────────────────────────────────


def _menu_add(monkeypatch, cron_cli, schedule):
    from rich import prompt

    from navig.commands import cron

    answers = iter([schedule, "navig backup export"])
    monkeypatch.setattr(prompt.Prompt, "ask", lambda *a, **k: next(answers))
    cron.add_cmd("menu job", {})


def test_menu_add_sends_a_plain_job_not_an_exec_one(monkeypatch, cron_cli):
    """Called as a function, omitted options were Typer's TRUTHY OptionInfo: every
    menu-created job was silently turned into an --exec job."""
    from navig.scheduler.cron_service import EXEC_PREFIX

    _, calls = cron_cli
    _menu_add(monkeypatch, cron_cli, "0 9 * * *")
    (body,) = calls
    assert body["command"] == "navig backup export"
    assert not body["command"].startswith(EXEC_PREFIX)
    assert "timeout" not in body and "yearly" not in body


def test_menu_add_is_held_to_the_date_pin_guard(monkeypatch, cron_cli):
    import typer

    _, calls = cron_cli
    with pytest.raises(typer.Exit):
        _menu_add(monkeypatch, cron_cli, "0 11 9 10 *")
    assert calls == []


# ── the agent's task_add tool ───────────────────────────────────────────────


@pytest.fixture
def board(tmp_path, monkeypatch):
    from navig.agent.tools import pim_tools
    from navig.pim import reminders
    from navig.store.board import BoardStore

    st = BoardStore(tmp_path / "board.db")
    monkeypatch.setattr(pim_tools, "_store", lambda: st)
    monkeypatch.setattr(reminders, "operator_delivery", lambda: None)
    return st


def _task_add(args):
    import asyncio

    from navig.agent.tools import pim_tools

    return asyncio.run(pim_tools.TaskAddTool().run(args))


def test_task_add_due_at_keeps_the_title_verbatim(board):
    title = "RDV 11h45 - apporter l IRM du 30/09"
    result = _task_add({"title": title, "due_at": "2099-10-12 09:00", "space": "health"})
    assert result.success, result.error
    (todo,) = board.list_todos()
    assert todo["title"] == title  # without due_at, "30/09" became the due date and was cut
    assert todo["due_at"].startswith("2099-10-12")
    assert todo["space"] == "health"


@pytest.mark.parametrize("due_at", ["2000-01-01 09:00", "whenever"])
def test_task_add_refuses_an_unusable_due_at_and_saves_nothing(board, due_at):
    result = _task_add({"title": "call back", "due_at": due_at})
    assert not result.success
    assert board.list_todos() == []


def test_task_add_without_due_at_still_reads_the_date_from_the_text(board):
    result = _task_add({"title": "Dentist oct 26 10:30"})
    assert result.success, result.error
    (todo,) = board.list_todos()
    assert todo["title"] == "Dentist"
    assert todo["due_at"]
