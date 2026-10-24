"""End-to-end coordination at the mailbox layer — two sessions, offline recovery, idempotency.

These exercise the real store + delivery worker + contract (no OS process): they prove the
authoritative mailbox supports a two-session exchange, that an offline recipient is pending
(not lost) and recovers when its handler registers, and that duplicate sends/deliveries stay
single. They are NOT a claim of live OS UI verification.
"""

from __future__ import annotations

import pytest

from navig.coordination import delivery, list_inbox, send
from navig.store.coordination import CoordinationStore


@pytest.fixture
def store(tmp_path):  # noqa: ANN001
    return CoordinationStore(tmp_path / "coordination.db")


@pytest.fixture(autouse=True)
def _clear_handlers():
    yield
    for prefix in list(delivery.handlers()):
        delivery.unregister_handler(prefix)


def test_two_sessions_exchange_and_offline_recovery(monkeypatch, store) -> None:
    monkeypatch.setattr("navig.store.coordination.get_coordination_store", lambda: store)

    inbox: dict[str, list[dict]] = {}

    def session_handler(message: dict) -> None:
        inbox.setdefault(message["recipient"], []).append(message)

    # session:1 → session:2 while session:2 has no handler (offline).
    first = send("session:1", "session:2", "request", {"q": "status?"}, store=store, idempotency_key="e2e-1")
    outcomes = delivery.tick(store=store)
    assert [o.state for o in outcomes] == ["pending"]
    assert store.get(first["id"])["delivery_state"] == "persisted"  # not lost, not delivered

    # session:2 comes online → the same durable message is delivered.
    delivery.register_handler("session", session_handler)
    delivery.tick(store=store)
    assert inbox.get("session:2", [])[0]["id"] == first["id"]
    assert store.get(first["id"])["delivery_state"] == "delivered"

    # session:2 answers back to session:1.
    second = send("session:2", "session:1", "answer", {"a": "ok"}, store=store)
    delivery.tick(store=store)
    assert inbox.get("session:1", [])[0]["id"] == second["id"]


def test_duplicate_send_and_delivery_stay_single(monkeypatch, store) -> None:
    monkeypatch.setattr("navig.store.coordination.get_coordination_store", lambda: store)

    calls: list[str] = []
    delivery.register_handler("session", lambda m: calls.append(m["id"]))

    a = send("session:1", "session:2", "result", {"n": 1}, store=store, idempotency_key="dup")
    b = send("session:1", "session:2", "result", {"n": 2}, store=store, idempotency_key="dup")

    assert a["id"] == b["id"]
    assert len(list_inbox("session:2", store=store)) == 1

    delivery.tick(store=store)
    delivery.tick(store=store)  # delivered rows are never re-selected
    assert calls == [a["id"]]


def test_unauthorized_or_invalid_messages_never_execute(monkeypatch, store) -> None:
    monkeypatch.setattr("navig.store.coordination.get_coordination_store", lambda: store)

    ran: list[str] = []
    delivery.register_handler("session", lambda m: ran.append(m["id"]))

    # Invalid sender/kind/self-delegation are refused at the contract, before delivery.
    from navig.coordination import CoordinationError

    with pytest.raises(CoordinationError):
        send("mallory", "session:2", "inform", {}, store=store)
    with pytest.raises(CoordinationError):
        send("session:1", "session:2", "run_arbitrary_code", {}, store=store)
    with pytest.raises(CoordinationError):
        send("session:1", "session:1", "delegate", {}, store=store)

    delivery.tick(store=store)
    assert ran == []
