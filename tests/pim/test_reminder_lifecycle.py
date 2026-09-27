"""Every surface that changes a task's schedule must re-derive its reminders.

`pim.reminders` was written to prevent the ghost ping — a reminder that arrives, by
name, for a task you finished yesterday. It does that job for the Telegram card and
did it for nothing else: `navig todo done|rm|when` and the agent's `task_done` all
mutated a todo and left its reminder rows pointing at the old state, and `task_add` /
`navig todo add --remind 3d` scheduled nothing at all. A comment in
`telegram_commands._handle_todo` records the reason — those surfaces "have no chat to
deliver to" — which is true of the surface and not of the operator, who has exactly
one Telegram chat and it is already in the config.

The other half is the Todo extension switch. Its banner promises that switching the
extension off stops delivery; habits enforce that promise by declining to write the
reminder row (`cron_service._execute_job_command`), and the todo extension copied the
promise without the enforcement. Both ends are covered here: no new row while it is
off, and a row written before the switch was flipped is held rather than delivered.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from navig.pim import reminders
from navig.pim.clock import to_utc_iso
from navig.store.board import BoardStore

TZ = timezone(timedelta(hours=2))
NOW = datetime(2026, 9, 4, 15, 0, tzinfo=TZ)
OPERATOR = 424242


class _FakeRuntime:
    """Stands in for RuntimeStore: records instead of writing."""

    def __init__(self) -> None:
        self.created: list[tuple[int, int, str, datetime]] = []
        self.cancelled: list[tuple[int, int]] = []
        self.next_id = 500
        self.cancel_returns = True

    def create_reminder(self, user_id: int, chat_id: int, message: str, remind_at: datetime) -> int:
        self.created.append((user_id, chat_id, message, remind_at))
        self.next_id += 1
        return self.next_id

    def cancel_reminder(self, reminder_id: int, user_id: int) -> bool:
        self.cancelled.append((reminder_id, user_id))
        return self.cancel_returns


@pytest.fixture
def runtime(monkeypatch) -> _FakeRuntime:
    fake = _FakeRuntime()
    monkeypatch.setattr(reminders, "_runtime_store", lambda: fake)
    return fake


@pytest.fixture
def store(tmp_path: Path) -> BoardStore:
    return BoardStore(tmp_path / "board.db")


@pytest.fixture
def operator(monkeypatch) -> int:
    """A configured Telegram operator, so the chatless surfaces have somewhere to send."""
    monkeypatch.setattr(
        "navig.messaging.notify_operator.resolve_operator_chat_id", lambda: str(OPERATOR)
    )
    return OPERATOR


@pytest.fixture
def extension_on(monkeypatch) -> None:
    monkeypatch.setattr(reminders, "_extension_is_off", lambda: False)


def _due(hours: float) -> str:
    return to_utc_iso(NOW + timedelta(hours=hours))


def _scheduled(store: BoardStore, card_id: str) -> list[int]:
    return store.todo_reminder_ids(card_id)


# ── who the chatless surfaces deliver to ─────────────────────────────────────


def test_operator_delivery_resolves_the_configured_chat(operator):
    """The CLI has no chat; the operator does, and it is already in the config."""
    assert reminders.operator_delivery() == (OPERATOR, OPERATOR)


def test_operator_delivery_is_none_without_telegram(monkeypatch):
    """A CLI-only machine is a normal install, not a broken one."""
    monkeypatch.setattr("navig.messaging.notify_operator.resolve_operator_chat_id", lambda: None)
    assert reminders.operator_delivery() is None


def test_operator_delivery_rejects_a_non_numeric_chat_id(monkeypatch):
    """`cancel_reminder` takes an int. A misconfigured value must not become one."""
    monkeypatch.setattr(
        "navig.messaging.notify_operator.resolve_operator_chat_id", lambda: "not-a-number"
    )
    assert reminders.operator_delivery() is None


def test_sync_for_operator_is_a_no_op_without_telegram(monkeypatch, store, runtime):
    """Nowhere to deliver means nothing to schedule — and no exception either."""
    monkeypatch.setattr(reminders, "operator_delivery", lambda: None)
    todo = store.create_todo("Dentist", due_at=_due(5))
    assert reminders.sync_for_operator(store, todo, now=NOW) == 0
    assert runtime.created == []


def test_sync_for_operator_tolerates_a_missing_todo(store):
    """`complete_todo` returns None for a row that vanished; that must not raise here."""
    assert reminders.sync_for_operator(store, None, now=NOW) == 0


# ── the CLI ──────────────────────────────────────────────────────────────────


def _cli(monkeypatch, store: BoardStore):
    """Point `navig todo` at this test's store and clock."""
    from navig.commands import todo as cli

    monkeypatch.setattr(cli, "_store", lambda: store)
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    monkeypatch.setattr("navig.store.board.get_board_store", lambda: store)
    return cli


def test_cli_add_with_a_date_schedules_reminders(
    monkeypatch, store, runtime, operator, extension_on
):
    cli = _cli(monkeypatch, store)
    cli.add_cmd(
        text=["Dentist", "tomorrow", "10:30"], category="", space=None, remind=[], as_json=False
    )

    todo = store.list_todos()[0]
    assert todo["due_at"], "the sentence carried a date"
    assert _scheduled(store, todo["id"]), "a dated task must have its reminder scheduled"
    assert runtime.created, "the reminder has to reach the reminder table"


def test_cli_add_honours_the_remind_flag(monkeypatch, store, runtime, operator, extension_on):
    """`-r 3d` was decorative: it was stored and never scheduled."""
    cli = _cli(monkeypatch, store)
    cli.add_cmd(
        text=["Renew", "the", "domain", "in", "10", "days"],
        category="",
        space=None,
        remind=["3d"],
        as_json=False,
    )
    todo = store.list_todos()[0]
    leads = [lead for _, _, message, _ in runtime.created for lead in [message]]
    assert len(runtime.created) == 2, f"a 3-day lead plus the due moment; got {leads}"


def test_cli_when_reschedules_instead_of_leaving_the_old_moment(
    monkeypatch, store, runtime, operator, extension_on
):
    """Moving the date is the commonest edit and the easiest ghost to create."""
    cli = _cli(monkeypatch, store)
    todo = store.create_todo("Dentist", due_at=_due(3))
    reminders.reschedule(store, todo, user_id=OPERATOR, chat_id=OPERATOR, now=NOW)
    first = _scheduled(store, todo["id"])
    assert first, "precondition: it starts with a reminder"

    cli.when_cmd(todo_id=todo["id"], when=["in", "8", "days"])

    assert runtime.cancelled, "the reminder for the OLD date must be cancelled"
    assert [rid for rid, _ in runtime.cancelled] == first
    assert _scheduled(store, todo["id"]) not in ([], first), "and a new one scheduled"


def test_cli_done_cancels_the_reminders(monkeypatch, store, runtime, operator, extension_on):
    """The headline ghost: ticked off here, still pinging from Telegram."""
    cli = _cli(monkeypatch, store)
    todo = store.create_todo("Dentist", due_at=_due(3))
    reminders.reschedule(store, todo, user_id=OPERATOR, chat_id=OPERATOR, now=NOW)
    assert _scheduled(store, todo["id"]), "precondition"

    cli.done_cmd(todo_id=todo["id"])

    assert runtime.cancelled, "a finished task must not remain scheduled"
    assert _scheduled(store, todo["id"]) == []


def test_cli_done_rolls_a_recurring_task_onto_its_next_reminder(
    monkeypatch, store, runtime, operator, extension_on
):
    """A recurring todo comes back OPEN on a new date — so it needs new reminders,
    not merely cancelled ones."""
    cli = _cli(monkeypatch, store)
    todo = store.create_todo("Bins out", due_at=_due(3), recur="weekly")
    reminders.reschedule(store, todo, user_id=OPERATOR, chat_id=OPERATOR, now=NOW)
    before = _scheduled(store, todo["id"])

    cli.done_cmd(todo_id=todo["id"])

    after = _scheduled(store, todo["id"])
    assert after, "a repeating task still has a next occurrence to warn about"
    assert after != before, "and it is a different reminder, for the new date"
    assert store.get_todo(todo["id"])["completed_at"] is None


def test_cli_rm_cancels_before_it_deletes(monkeypatch, store, runtime, operator, extension_on):
    """The links cascade with the card, so deleting first loses the only record of
    what still needs cancelling."""
    cli = _cli(monkeypatch, store)
    todo = store.create_todo("Dentist", due_at=_due(3))
    reminders.reschedule(store, todo, user_id=OPERATOR, chat_id=OPERATOR, now=NOW)
    ids = _scheduled(store, todo["id"])
    assert ids, "precondition"

    cli.rm_cmd(todo_id=todo["id"], yes=True)

    assert [rid for rid, _ in runtime.cancelled] == ids, (
        "every reminder for a deleted task must be cancelled — nothing else can "
        "ever find them again"
    )
    assert store.get_todo(todo["id"]) is None


def test_cli_works_with_no_telegram_configured(monkeypatch, store, runtime, extension_on):
    """A CLI-only machine must still be able to finish and delete tasks."""
    monkeypatch.setattr(reminders, "operator_delivery", lambda: None)
    cli = _cli(monkeypatch, store)
    todo = store.create_todo("Dentist", due_at=_due(3))

    cli.done_cmd(todo_id=todo["id"])
    assert store.get_todo(todo["id"])["completed_at"]


# ── the agent tools ──────────────────────────────────────────────────────────


def _run(tool, args: dict[str, Any]):
    return asyncio.run(tool.run(args))


def test_agent_task_add_schedules_what_it_promises(
    monkeypatch, store, runtime, operator, extension_on
):
    """An agent that adds 'Dentist tomorrow 10:30' and schedules nothing has written
    a note, not a reminder."""
    from navig.agent.tools import pim_tools

    monkeypatch.setattr(pim_tools, "_store", lambda: store)
    result = _run(pim_tools.TaskAddTool(), {"title": "Dentist tomorrow 10:30"})

    assert result.success, result.error
    todo = store.list_todos()[0]
    assert _scheduled(store, todo["id"]), "a dated task the agent added must ping too"


def test_agent_task_done_cancels_the_reminders(monkeypatch, store, runtime, operator, extension_on):
    from navig.agent.tools import pim_tools

    monkeypatch.setattr(pim_tools, "_store", lambda: store)
    todo = store.create_todo("Dentist", due_at=_due(3))
    reminders.reschedule(store, todo, user_id=OPERATOR, chat_id=OPERATOR, now=NOW)
    assert _scheduled(store, todo["id"]), "precondition"

    result = _run(pim_tools.TaskDoneTool(), {"id": todo["id"]})

    assert result.success, result.error
    assert runtime.cancelled, "an agent ticking a task off must not leave it pinging"
    assert _scheduled(store, todo["id"]) == []


# ── the extension switch actually stops delivery ─────────────────────────────


def test_nothing_is_scheduled_while_the_extension_is_off(monkeypatch, store, runtime, operator):
    """The habit rule: no row is written, so nothing can ever be delivered."""
    monkeypatch.setattr(reminders, "_extension_is_off", lambda: True)
    todo = store.create_todo("Dentist", due_at=_due(3))

    assert reminders.reschedule(store, todo, user_id=OPERATOR, chat_id=OPERATOR, now=NOW) == 0
    assert runtime.created == []
    assert _scheduled(store, todo["id"]) == []


def test_switching_the_extension_off_clears_what_was_already_scheduled(
    monkeypatch, store, runtime, operator, extension_on
):
    """The cancel half still runs while off — the next edit tidies up after the switch."""
    todo = store.create_todo("Dentist", due_at=_due(3))
    reminders.reschedule(store, todo, user_id=OPERATOR, chat_id=OPERATOR, now=NOW)
    assert _scheduled(store, todo["id"]), "precondition"

    monkeypatch.setattr(reminders, "_extension_is_off", lambda: True)
    reminders.reschedule(store, todo, user_id=OPERATOR, chat_id=OPERATOR, now=NOW)

    assert runtime.cancelled, "the old rows go, even though no new ones are written"
    assert _scheduled(store, todo["id"]) == []


def test_delivery_is_suppressed_only_for_todo_reminders_and_only_while_off(
    monkeypatch, store, runtime, operator, extension_on
):
    """A `/remindme` ping must be untouched by the Todo switch."""
    todo = store.create_todo("Dentist", due_at=_due(3))
    reminders.reschedule(store, todo, user_id=OPERATOR, chat_id=OPERATOR, now=NOW)
    todo_rid = _scheduled(store, todo["id"])[0]
    monkeypatch.setattr("navig.store.board.get_board_store", lambda: store)

    assert reminders.delivery_suppressed(todo_rid) is False, "the extension is ON"

    monkeypatch.setattr(reminders, "_extension_is_off", lambda: True)
    assert reminders.delivery_suppressed(todo_rid) is True
    assert reminders.delivery_suppressed(999_999) is False, (
        "a reminder that belongs to no todo is somebody else's — /remindme, a habit, "
        "a deck app — and the Todo switch must not silence it"
    )


def test_delivery_suppression_fails_open(monkeypatch):
    """A gate that cannot be read delivers. A missed ping is worse than one from a
    surface the operator had switched off."""
    monkeypatch.setattr(reminders, "_extension_is_off", lambda: True)

    def _boom():
        raise RuntimeError("board unavailable")

    monkeypatch.setattr("navig.store.board.get_board_store", _boom)
    assert reminders.delivery_suppressed(1) is False


def test_extension_gate_itself_fails_open(monkeypatch):
    """Same rule one level up: an unreadable extension registry must not mute the list."""
    import navig.telegram.todo_actions as todo_actions

    def _boom() -> bool:
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(todo_actions, "extension_is_off", _boom)
    assert reminders._extension_is_off() is False


def test_the_poller_actually_consults_the_gate(monkeypatch):
    """The gate exists; this is the half that proves something CALLS it.

    `_deliver_one_reminder` settles every reminder it is handed — delivered, retried
    or failed. A held one must come back untouched, so it is still there when the
    extension is switched on again.
    """
    from unittest.mock import MagicMock

    from navig.gateway.channels.telegram import TelegramChannel

    monkeypatch.setattr(reminders, "delivery_suppressed", lambda rid: True)
    store = MagicMock()
    channel = MagicMock()

    asyncio.run(
        TelegramChannel._deliver_one_reminder(
            channel,
            store,
            {"id": 77, "chat_id": OPERATOR, "message": "⏰ Dentist", "remind_at": _due(0)},
        )
    )

    store.complete_reminder.assert_not_called()
    store.fail_reminder.assert_not_called()
    channel.send_message.assert_not_called()


def test_the_poller_settles_the_same_reminder_when_nothing_is_suppressed(monkeypatch):
    """The contrast case, on the IDENTICAL input: a gate stuck on True would pass the
    test above while silencing every reminder in the product.

    This one is old enough to take the poller's 24-hour staleness path, so it settles
    without needing a real send — the point being only that it is settled at all,
    which the held one above must not be.
    """
    from unittest.mock import MagicMock

    from navig.gateway.channels.telegram import TelegramChannel

    monkeypatch.setattr(reminders, "delivery_suppressed", lambda rid: False)
    store = MagicMock()
    channel = MagicMock()

    asyncio.run(
        TelegramChannel._deliver_one_reminder(
            channel,
            store,
            {"id": 77, "chat_id": OPERATOR, "message": "⏰ Dentist", "remind_at": _due(0)},
        )
    )

    assert store.fail_reminder.called or store.complete_reminder.called, (
        "an unsuppressed reminder must be acted on, not silently dropped"
    )


# ── cancellation must stay recoverable ───────────────────────────────────────


def test_links_survive_a_failure_to_cancel(monkeypatch, store, runtime, operator, extension_on):
    """The link is the only record that a reminder belongs to a todo. Dropping it
    before the row is cancelled turns a retryable failure into a ping nobody can
    trace back to anything."""
    todo = store.create_todo("Dentist", due_at=_due(3))
    reminders.reschedule(store, todo, user_id=OPERATOR, chat_id=OPERATOR, now=NOW)
    ids = _scheduled(store, todo["id"])
    assert ids, "precondition"

    monkeypatch.setattr(reminders, "_runtime_store", lambda: None)
    assert reminders.cancel_for(store, todo["id"], user_id=OPERATOR) == 0
    assert _scheduled(store, todo["id"]) == ids, (
        "the reminder table was unreachable, so the links must still be there for the next attempt"
    )


def test_a_row_that_refuses_to_cancel_is_reported(
    monkeypatch, store, runtime, operator, extension_on, caplog
):
    """`cancel_reminder` is scoped by user_id, so 'no such row' and 'not yours' look
    identical from here — and a silent 0 is how a ghost becomes unfindable."""
    todo = store.create_todo("Dentist", due_at=_due(3))
    reminders.reschedule(store, todo, user_id=OPERATOR, chat_id=OPERATOR, now=NOW)
    runtime.cancel_returns = False

    with caplog.at_level("WARNING", logger=reminders.logger.name):
        assert reminders.cancel_for(store, todo["id"], user_id=OPERATOR) == 0

    messages = [r.getMessage() for r in caplog.records]
    assert any("may still fire" in m for m in messages), (
        f"nothing warned about the surviving rows: {messages}"
    )
    assert any(todo["id"] in m for m in messages), (
        "the warning has to name the task, or there is no way to find the rows it "
        f"is warning about: {messages}"
    )
