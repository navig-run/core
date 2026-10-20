"""`pim.service` — the one write path every Planner surface uses.

What matters is that every mutation leaves the reminders right: a moved, finished or
deleted item must never keep pinging, and a shared list's items must ping THAT chat.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from navig.pim import reminders as pim_reminders
from navig.pim import service
from navig.pim.clock import LOCAL, to_utc_iso
from navig.store.board import BoardStore

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=LOCAL)
OPERATOR = 7


class _Runtime:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}

    def create_reminder(self, user_id, chat_id, message, remind_at, *, bcid=None):  # noqa: ANN001
        rid = len(self.rows) + 1
        while rid in self.rows:
            rid += 1
        self.rows[rid] = {
            "id": rid, "user_id": user_id, "chat_id": chat_id, "message": message,
            "remind_at": to_utc_iso(remind_at), "bcid": bcid, "completed": 0,
        }
        return rid

    def cancel_reminder(self, reminder_id, user_id):  # noqa: ANN001
        row = self.rows.get(reminder_id)
        if row and row["user_id"] == user_id:
            del self.rows[reminder_id]
            return True
        return False

    def get_reminder(self, reminder_id):  # noqa: ANN001
        return self.rows.get(int(reminder_id))

    def list_upcoming(self, limit=50):  # noqa: ANN001
        return sorted(self.rows.values(), key=lambda r: r["remind_at"])[:limit]


@pytest.fixture
def env(tmp_path: Path, monkeypatch) -> tuple[BoardStore, _Runtime]:
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
    # Never read the operator's real business-reminder file from a test.
    monkeypatch.setattr("navig.telegram.biz_reminders.list_all", lambda: [])
    return store, runtime


def test_hash_picks_the_category_and_the_date_comes_off_the_end(env) -> None:
    store, runtime = env
    item = service.create_task("Invoice ACME #work friday 9am")
    assert item["title"] == "Invoice ACME"
    assert item["category"] == "work"
    assert item["due_at"] is not None
    assert len(runtime.rows) == 1  # the due-moment ping

    invented = service.create_task("Logo draft #freelance")
    assert invented["category"] == "freelance"
    assert store.get_category("freelance") is not None  # created lazily


def test_every_mutation_leaves_the_reminders_right(env) -> None:
    store, runtime = env
    item = service.create_task(title="Report", due_at=NOW + timedelta(days=5), leads=["3d", "1d"])
    assert len(runtime.rows) == 3

    service.update(item["id"], {"due_at": NOW + timedelta(days=10)})
    assert len(runtime.rows) == 3  # cancelled and re-made, never doubled

    service.complete(item["id"])
    assert runtime.rows == {}  # a finished task pings no one

    service.reopen(item["id"])
    assert len(runtime.rows) == 3

    service.delete(item["id"])
    assert runtime.rows == {}
    assert store.get_todo(item["id"]) is None


def test_a_shared_lists_item_pings_its_business_chat(env) -> None:
    store, runtime = env
    service.ensure_biz_list(555, bcid="BC", owner_id=OPERATOR, peer_name="Sarah")
    item = service.create_task(title="Bring the contract", list_id="tg:555", due_at=NOW + timedelta(hours=3))
    (row,) = runtime.rows.values()
    assert (row["chat_id"], row["bcid"]) == (555, "BC")

    service.move(item["id"], "me")
    (row,) = runtime.rows.values()
    assert (row["chat_id"], row["bcid"]) == (OPERATOR, None)  # moved → pings the DM now


def test_only_the_owner_deletes(env) -> None:
    item = service.create_task(title="x")
    with pytest.raises(service.Forbidden):
        service.delete(item["id"], actor_is_owner=False)


def test_a_proposed_appointment_pings_no_one_until_accepted(env) -> None:
    _, runtime = env
    appt = service.create_appointment("Coffee with Marc", NOW + timedelta(days=3), origin="ai")
    assert runtime.rows == {}
    service.accept(appt["id"])
    assert len(runtime.rows) == 3  # 1 day, 1 hour, and the moment


def test_cancelling_a_lead_ping_switches_the_lead_off(env) -> None:
    """Cancelling only the row would bring it back on the next edit of the item."""
    store, runtime = env
    item = service.create_task(title="Report", due_at=NOW + timedelta(days=5), leads=["3d", "1d"])
    lead_row = next(
        rid for rid, link in store.reminder_links(list(runtime.rows)).items() if link["lead"] == "3d"
    )
    service.cancel_reminder("runtime", str(lead_row))
    assert store.get_todo(item["id"])["remind_before"] == ["1d"]
    assert len(runtime.rows) == 2
    service.update(item["id"], {"title": "Report v2"})
    assert len(runtime.rows) == 2  # it did not come back


def test_snooze_moves_the_ping_and_keeps_its_task(env) -> None:
    store, runtime = env
    item = service.create_task(title="Call", due_at=NOW + timedelta(hours=1))
    (rid,) = runtime.rows
    out = service.snooze_reminder("runtime", str(rid), 1800)
    assert rid not in runtime.rows
    (new_row,) = runtime.rows.values()
    assert store.reminder_links([new_row["id"]])[new_row["id"]]["card_id"] == item["id"]
    assert out["source"] == "runtime"
    with pytest.raises(service.PlannerError):
        service.snooze_reminder("runtime", str(new_row["id"]), 10)


def test_an_appointment_cannot_end_before_it_starts(env) -> None:
    with pytest.raises(service.PlannerError):
        service.create_appointment("x", NOW + timedelta(hours=2), end_at=NOW + timedelta(hours=1))


def test_today_groups_by_the_operators_day(env) -> None:
    service.create_task(title="late", due_at=NOW - timedelta(hours=3))
    service.create_task(title="later today", due_at=NOW + timedelta(hours=3))
    service.create_task(title="undated")
    service.create_appointment("Dentist", NOW + timedelta(days=2))
    service.create_appointment("Maybe", NOW + timedelta(days=1), origin="ai")
    out = service.today(NOW)
    assert [t["title"] for t in out["overdue"]] == ["late"]
    assert [t["title"] for t in out["today"]] == ["later today"]
    assert out["inbox_count"] == 1
    assert [a["title"] for a in out["appointments"]] == ["Dentist"]
    assert [a["title"] for a in out["proposals"]] == ["Maybe"]
    assert out["appointments"][0]["day"] == (NOW + timedelta(days=2)).date().isoformat()


def test_a_naive_form_time_is_the_operators_wall_clock(env) -> None:
    """A browser form sends "2026-10-26T10:30" with no zone. That is 10:30 HERE, and
    it must stay 10:30 after the clocks change on the 25th."""
    appt = service.create_appointment("Visio", "2026-10-26T10:30")
    presented = service.present(appt, NOW)
    assert presented["due_local"].startswith("2026-10-26T10:30")


def test_reminders_lists_every_source_with_its_task(env) -> None:
    item = service.create_task(title="Report", due_at=NOW + timedelta(days=5), leads=["1d"])
    rows = service.reminders()
    assert [r["card_id"] for r in rows] == [item["id"], item["id"]]
    assert rows[0]["lead"] == "1d" and rows[0]["title"] == "Report"
