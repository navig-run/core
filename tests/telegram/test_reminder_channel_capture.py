"""A post in the Reminders channel becomes a task with a reminder — and only that
channel's posts do."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from navig.pim import reminders as pim_reminders
from navig.pim import service
from navig.pim.clock import LOCAL, to_utc_iso
from navig.store.board import BoardStore

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=LOCAL)
OPERATOR = 7
REMINDERS = -1004425457788


class _Runtime:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}

    def create_reminder(self, user_id, chat_id, message, remind_at, *, bcid=None):  # noqa: ANN001
        rid = len(self.rows) + 1
        self.rows[rid] = {"id": rid, "user_id": user_id, "chat_id": chat_id, "message": message,
                          "remind_at": to_utc_iso(remind_at), "bcid": bcid, "completed": 0}
        return rid

    def cancel_reminder(self, reminder_id, user_id):  # noqa: ANN001
        return self.rows.pop(reminder_id, None) is not None

    def get_reminder(self, reminder_id):  # noqa: ANN001
        return self.rows.get(int(reminder_id))

    def list_upcoming(self, limit=50):  # noqa: ANN001
        return list(self.rows.values())[:limit]


class _Channel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def _api_call(self, method, payload):  # noqa: ANN001
        self.calls.append((method, payload))
        return {"message_id": 99}


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    store = BoardStore(tmp_path / "board.db")
    runtime = _Runtime()
    monkeypatch.setattr(service, "_store", lambda: store)
    monkeypatch.setattr(service, "_runtime", lambda: runtime)
    monkeypatch.setattr(pim_reminders, "_runtime_store", lambda: runtime)
    monkeypatch.setattr(pim_reminders, "_extension_is_off", lambda: False)
    monkeypatch.setattr(pim_reminders, "operator_delivery", lambda: (OPERATOR, OPERATOR))
    monkeypatch.setattr("navig.pim.clock.local_now", lambda: NOW)
    monkeypatch.setattr(service, "local_now", lambda: NOW)
    monkeypatch.setattr(service, "_SINK", None)
    monkeypatch.setattr("navig.telegram.biz_reminders.list_all", lambda: [])

    from navig.notify import telegram_routes
    from navig.notify.telegram_routes import Route

    route: dict[str, Any] = {"r": Route(REMINDERS, "Reminders Dispatch")}
    monkeypatch.setattr(telegram_routes, "get_route", lambda t: route["r"] if t == "reminder" else None)
    return store, runtime, route


def _post(text: str, *, chat: int = REMINDERS, mid: int = 5) -> dict:
    return {"message_id": mid, "chat": {"id": chat, "type": "channel"}, "text": text}


def _run(post: dict) -> tuple[bool, _Channel]:
    from navig.telegram.channel_capture import handle_channel_post

    ch = _Channel()
    return asyncio.run(handle_channel_post(ch, post)), ch


def test_a_post_with_a_time_becomes_a_task_with_a_reminder(env) -> None:
    store, runtime, _ = env
    consumed, ch = _run(_post("dentist #health friday 9am"))
    assert consumed is True
    todos = store.list_todos()
    assert [t["title"] for t in todos] == ["dentist"]
    assert todos[0]["due_at"] is not None
    # The reminder goes to the operator's DM — the `reminder` route then carries it
    # back into this channel when it fires.
    assert [r["chat_id"] for r in runtime.rows.values()] == [OPERATOR]
    method, payload = ch.calls[0]
    assert method == "sendMessage" and payload["chat_id"] == REMINDERS
    assert payload["text"].startswith("✅ Reminder set")
    assert payload["disable_notification"] is True, "the confirmation must not ring"
    assert payload["reply_to_message_id"] == 5


def test_a_post_without_a_time_is_saved_and_says_so(env) -> None:
    store, runtime, _ = env
    _consumed, ch = _run(_post("buy printer ink"))
    assert [t["title"] for t in store.list_todos()] == ["buy printer ink"]
    assert runtime.rows == {}
    assert "No time found" in ch.calls[0][1]["text"]


def test_a_redelivered_post_is_captured_once(env) -> None:
    store, runtime, _ = env
    _run(_post("call mom friday 6pm", mid=11))
    _consumed, ch = _run(_post("call mom friday 6pm", mid=11))
    assert len(store.list_todos()) == 1 and len(runtime.rows) == 1
    assert ch.calls == [], "a duplicate says nothing a second time"


def test_posts_in_any_other_channel_are_not_touched(env) -> None:
    store, _runtime, route = env
    consumed, ch = _run(_post("dentist friday 9am", chat=-1004466964318))
    assert consumed is False and store.list_todos() == [] and ch.calls == []

    route["r"] = None  # no reminder channel configured at all
    consumed, _ch = _run(_post("dentist friday 9am"))
    assert consumed is False and store.list_todos() == []


def test_navigs_own_cards_and_commands_are_never_captured(env) -> None:
    store, _runtime, _ = env
    for text in ("⏰ Reminder\ndentist", "✅ Reminder set — Fri 9:00", "/start", ""):
        consumed, ch = _run(_post(text))
        assert consumed is True and ch.calls == []
    assert store.list_todos() == []


# ── reply-to-manage: answer a confirmation with done / cancel / snooze ───────


def _reply(env, task_reply_id: int, text: str) -> tuple[bool, str]:
    """Post *text* as a reply to *task_reply_id* in the reminder channel."""
    from navig.telegram.channel_capture import handle_channel_post

    ch = _Channel()
    post = _post(text, mid=77)
    # The real Bot API shape: the replied-to message is nested, never a top-level id.
    post["reply_to_message"] = {"message_id": task_reply_id, "chat": {"id": REMINDERS}}
    consumed = asyncio.run(handle_channel_post(ch, post))
    return consumed, (ch.calls[0][1]["text"] if ch.calls else "")


def _make_task(env, text: str = "dentist friday 9am") -> tuple[str, int]:
    """Capture a post and return (task_id, the confirmation's message id)."""

    store, runtime, _route = env
    result = capture_post(REMINDERS, 5, text)
    assert result["status"] == "scheduled", result
    task_id = store.list_todos()[0]["id"]
    # The confirmation is sent with message_id 99 by the fake channel.
    return task_id, 99


def test_capture_maps_the_confirmation_to_the_task(env):
    store, _runtime, _route = env
    _consumed, _ch = _run(_post("dentist friday 9am", mid=5))
    from navig.telegram.channel_capture import _target_task

    task_id = store.list_todos()[0]["id"]
    # The confirmation the fake channel returned carries message_id 99.
    assert _target_task(REMINDERS, 99) == task_id
    assert _target_task(REMINDERS, 5) == task_id, "the operator's own post maps too"


def test_reply_done_completes_the_task(env):
    store, _runtime, _route = env
    _run(_post("dentist friday 9am", mid=5))
    consumed, reply = _reply(env, 99, "done")
    assert consumed is True
    assert reply.startswith("✅ Done"), reply
    # list_todos() shows open items only, so a completed task leaves the list…
    assert store.list_todos() == []
    # …and is still readable as done when the operator answers again —
    # completion is reported as `completed_at` (there is no `done` on a todo).
    done_item = store.list_todos(include_done=True)[0]
    assert done_item.get("completed_at"), done_item


def test_reply_cancel_removes_the_task(env):
    store, _runtime, _route = env
    _run(_post("dentist friday 9am", mid=5))
    consumed, reply = _reply(env, 99, "cancel")
    assert consumed is True and reply.startswith("🗑 Removed"), reply
    assert store.list_todos() == []


def test_reply_snooze_moves_the_reminder(env):
    store, runtime, _route = env
    _run(_post("dentist friday 9am", mid=5))
    task_id = store.list_todos()[0]["id"]
    before = store.list_todos()[0]["due_at"]
    consumed, reply = _reply(env, 99, "snooze 2h")
    assert consumed is True and reply.startswith("⏰ Moved to"), reply
    after = store.list_todos()[0]["due_at"]
    assert after != before, "the due time did not move"
    # The reminder table was rescheduled by the same update path.
    assert len(runtime.rows) >= 1


def test_snoozing_an_overdue_task_counts_from_now(env):
    """A reply that arrives after the due time snoozes from NOW, not from the stale
    due time — 09:00 + 1h answered at 12:00 was 10:00, a past time no reminder fires for."""
    from datetime import UTC, timedelta

    from navig.pim.clock import from_utc_iso

    store, _runtime, _route = env
    _run(_post("dentist friday 9am", mid=5))
    task_id = store.list_todos()[0]["id"]
    service.update(task_id, {"due_at": NOW - timedelta(hours=3)})

    consumed, reply = _reply(env, 99, "snooze 1h")
    assert consumed is True and reply.startswith("⏰ Moved to"), reply
    after = from_utc_iso(store.list_todos()[0]["due_at"])
    assert after == (NOW + timedelta(hours=1)).astimezone(UTC), after


def test_a_reply_that_is_not_a_command_does_not_become_a_task(env):
    store, _runtime, _route = env
    _run(_post("dentist friday 9am", mid=5))
    consumed, reply = _reply(env, 99, "thanks a lot")
    assert consumed is True
    assert "done" in reply and "cancel" in reply, reply  # the hint
    assert len(store.list_todos()) == 1, "a chatty reply must not add a task"


def test_a_command_replying_to_an_unknown_message_explains_itself(env):
    store, _runtime, _route = env
    consumed, reply = _reply(env, 4242, "done")
    assert consumed is True
    assert "confirmation" in reply.lower(), reply
    assert store.list_todos() == []


def test_a_reply_outside_the_reminder_channel_is_untouched(env):
    store, _runtime, route = env
    route["r"] = None  # no reminder channel configured
    consumed, reply = _reply(env, 99, "done")
    assert consumed is False and reply == "" and store.list_todos() == []


def test_snoozing_an_already_completed_task_says_so(env):
    """A dead guard is worse than none: `get_todo` has no `done` field, so the
    original `item.get("done")` was always None and a finished task could be
    un-completed by snoozing it."""
    store, _runtime, _route = env
    _run(_post("dentist friday 9am", mid=5))
    task_id = store.list_todos()[0]["id"]
    store.complete_todo(task_id)

    consumed, reply = _reply(env, 99, "snooze 1h")
    assert consumed is True and reply.startswith("Already done"), reply
    assert store.list_todos(include_done=True)[0].get("completed_at")


def test_the_reply_mapping_is_pruned(env):
    """The mapping is one row per confirmation. An earlier version compared
    `created_at < MIN(created_at)` — never true — so it pruned NOTHING and the
    table grew forever."""
    from navig.notify import store as nstore
    from navig.telegram.channel_capture import _PRUNE_KEEP, _remember, _target_task

    _run(_post("first task", mid=1))
    n = _PRUNE_KEEP + 50
    for i in range(n):
        _remember(REMINDERS, 10_000 + i, f"task-{i}", now=f"2026-10-{(i % 9) + 1:02d}T00:00:00+00:00")
    kept = nstore.conn().execute(
        "SELECT COUNT(*) AS n FROM notify_tg_chan_targets WHERE chat_id = ?", (REMINDERS,)
    ).fetchone()["n"]
    assert kept <= _PRUNE_KEEP + 2, f"mapping grew to {kept} rows"
    # The newest ones survived, so a recent reply still resolves.
    assert _target_task(REMINDERS, 10_000 + n - 1) == f"task-{n - 1}"
