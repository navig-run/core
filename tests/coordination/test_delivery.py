"""Delivery worker — at-least-once routing, retry/backoff, expiry, offline recovery."""

from __future__ import annotations

import time

import pytest

from navig.coordination import delivery, send
from navig.store.coordination import CoordinationStore


@pytest.fixture
def store(tmp_path):  # noqa: ANN001
    return CoordinationStore(tmp_path / "coordination.db")


@pytest.fixture(autouse=True)
def _clear_handlers():
    yield
    for prefix in list(delivery.handlers()):
        delivery.unregister_handler(prefix)


def _iso(seconds_from_now: float) -> str:
    from datetime import datetime, timedelta, timezone

    dt = datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)
    return dt.isoformat()


def test_delivered_once_to_a_registered_handler(store) -> None:
    received: list[dict] = []
    delivery.register_handler("session", received.append)

    m = send("agent:a", "session:1", "inform", {"note": "hi"}, store=store)

    first = delivery.tick(store=store)
    assert [o.state for o in first] == ["delivered"]
    assert len(received) == 1

    delivery.tick(store=store)  # delivered rows are not re-selected
    assert len(received) == 1
    assert store.get(m["id"])["delivery_state"] == "delivered"


def test_offline_recipient_is_pending_not_lost_and_recovers(store) -> None:
    m = send("agent:a", "session:1", "question", {}, store=store)

    first = delivery.tick(store=store)
    assert first[0].state == "pending"
    assert "no handler" in first[0].error
    assert store.get(m["id"])["delivery_state"] == "persisted"

    received: list[dict] = []
    delivery.register_handler("session", received.append)
    delivery.tick(store=store)
    assert len(received) == 1
    assert store.get(m["id"])["delivery_state"] == "delivered"


def test_bounded_retries_dead_letter(store, monkeypatch) -> None:
    monkeypatch.setattr(delivery, "RETRY_BASE_SECONDS", 0.0)
    delivery.register_handler("session", lambda _m: (_ for _ in ()).throw(RuntimeError("boom")))

    m = send("agent:a", "session:1", "delegate", {}, store=store)

    states = []
    for _ in range(delivery.MAX_RETRIES + 1):
        delivery.tick(store=store)
        states.append(store.get(m["id"])["delivery_state"])

    assert states[-1] == "failed"
    assert store.get(m["id"])["retry_count"] == delivery.MAX_RETRIES


def test_expired_messages_never_fire_a_handler(store) -> None:
    called: list[dict] = []
    delivery.register_handler("session", called.append)

    m = send("agent:a", "session:1", "request", {}, store=store, expires_at=_iso(-10))

    outcomes = delivery.tick(store=store, now_ts=time.time())
    assert [o.state for o in outcomes] == ["expired"]
    assert called == []
    assert store.get(m["id"])["delivery_state"] == "expired"


def test_retry_backoff_defers_a_failed_attempt(store, monkeypatch) -> None:
    monkeypatch.setattr(delivery, "RETRY_BASE_SECONDS", 60.0)
    delivery.register_handler("session", lambda _m: (_ for _ in ()).throw(RuntimeError("boom")))

    m = send("agent:a", "session:1", "inform", {}, store=store)
    now = time.time()

    delivery.tick(store=store, now_ts=now)      # attempt 1, fails, backoff
    delivery.tick(store=store, now_ts=now + 1)  # within backoff → not selected
    assert store.get(m["id"])["retry_count"] == 1
