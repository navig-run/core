"""Shared-list pings land in their Business chat — once, and never retried into a refusal."""

from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from navig.gateway.channels.telegram import TelegramChannel
from navig.store.runtime import RuntimeStore


class _Channel:
    def __init__(self, *, business_ok: bool = True) -> None:
        self.business_ok = business_ok
        self.api: list[tuple[str, dict[str, Any]]] = []
        self.dm: list[tuple[int, str]] = []
        self._deliver_business_reminder = types.MethodType(
            TelegramChannel._deliver_business_reminder, self
        )

    async def _api_call(self, method: str, data: dict[str, Any]) -> Any:
        self.api.append((method, data))
        return {"message_id": 1} if self.business_ok else None

    async def send_message(self, chat_id, text, parse_mode=None):  # noqa: ANN001
        self.dm.append((chat_id, text))
        return {"message_id": 2}


@pytest.fixture(autouse=True)
def _quiet(monkeypatch) -> None:
    monkeypatch.setattr("navig.pim.reminders.delivery_suppressed", lambda _rid: False)
    monkeypatch.setattr(
        "navig.gateway.channels.telegram_extensions.is_enabled", lambda ext: True
    )


def _due_row(store: RuntimeStore, *, bcid: str | None = "BC") -> dict[str, Any]:
    rid = store.create_reminder(
        7, 555, "Bring the contract", datetime.now(timezone.utc) - timedelta(seconds=5), bcid=bcid
    )
    return next(r for r in store.get_due_reminders() if r["id"] == rid)


async def test_a_shared_item_is_posted_into_its_business_chat(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    row = _due_row(store)
    ch = _Channel()
    await TelegramChannel._deliver_one_reminder(ch, store, row)
    (method, data), = ch.api
    assert method == "sendMessage"
    assert (data["chat_id"], data["business_connection_id"]) == (555, "BC")
    assert ch.dm == []
    assert store.get_reminder(row["id"])["completed"] == 1


async def test_a_refusal_goes_to_the_owner_and_is_not_retried(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    row = _due_row(store)
    ch = _Channel(business_ok=False)
    await TelegramChannel._deliver_one_reminder(ch, store, row)
    assert [chat for chat, _ in ch.dm] == [7]  # the owner's DM
    assert "Bring the contract" in ch.dm[0][1]
    assert store.get_reminder(row["id"])["completed"] == 1  # settled, no retry loop


async def test_two_processes_deliver_one_ping_once(tmp_path: Path) -> None:
    """Both gateway processes poll the table. Without the claim, each read the due row
    before either completed it — the ping went out twice."""
    path = tmp_path / "runtime.db"
    a, b = RuntimeStore(path), RuntimeStore(path)
    row = _due_row(a)
    ch = _Channel()
    await TelegramChannel._deliver_one_reminder(ch, a, dict(row))
    await TelegramChannel._deliver_one_reminder(ch, b, dict(row))
    assert len([m for m, _ in ch.api if m == "sendMessage"]) == 1


async def test_with_the_business_extension_off_it_goes_to_the_dm(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "navig.gateway.channels.telegram_extensions.is_enabled", lambda ext: ext != "business"
    )
    store = RuntimeStore(tmp_path / "runtime.db")
    row = _due_row(store)
    ch = _Channel()
    await TelegramChannel._deliver_one_reminder(ch, store, row)
    assert ch.api == []
    assert [chat for chat, _ in ch.dm] == [7]


def test_the_runtime_v3_migration_adds_the_columns(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "runtime.db"
    first = RuntimeStore(path)
    first.close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_version SET version = 2")
    conn.commit()
    conn.close()
    store = RuntimeStore(path)  # re-runs v2 -> v3 over existing columns: must not raise
    # The CURRENT version, not a literal: v3 -> v4 landed later and a pinned `== 3` went
    # red on main with nothing selecting it. Actual == declared still fails if a step is lost.
    assert store.get_schema_version() == RuntimeStore.SCHEMA_VERSION
    rid = store.create_reminder(1, 2, "x", datetime.now(timezone.utc), bcid="B")
    assert store.get_reminder(rid)["bcid"] == "B"
