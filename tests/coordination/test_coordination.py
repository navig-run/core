"""Coordination service — the message contract, validation and delivery-state machine."""

from __future__ import annotations

import pytest

from navig.coordination import (
    CoordinationError,
    ack,
    act,
    cancel,
    consume,
    deliver,
    expire,
    list_inbox,
    send,
)
from navig.store.coordination import CoordinationStore


@pytest.fixture
def store(tmp_path):  # noqa: ANN001
    return CoordinationStore(tmp_path / "coordination.db")


def test_send_validates_participants_kinds_and_self_delegation(store) -> None:
    with pytest.raises(CoordinationError):
        send("bogus", "session:1", "inform", {}, store=store)
    with pytest.raises(CoordinationError):
        send("agent:a", "bogus", "inform", {}, store=store)
    with pytest.raises(CoordinationError):
        send("agent:a", "session:1", "nonsense", {}, store=store)
    with pytest.raises(CoordinationError):
        send("session:1", "session:1", "delegate", {}, store=store)


def test_send_requires_object_content(store) -> None:
    with pytest.raises(CoordinationError):
        send("agent:a", "session:1", "inform", "not-an-object", store=store)  # type: ignore[arg-type]


def test_send_persists_and_is_idempotent_by_key(store) -> None:
    first = send("agent:a", "session:1", "inform", {"k": "v"}, store=store, idempotency_key="k")
    second = send("agent:a", "session:1", "inform", {"k": "v"}, store=store, idempotency_key="k")
    assert first["id"] == second["id"]
    assert first["delivery_state"] == "persisted"
    assert second["delivery_state"] == "persisted"


def test_delivery_state_machine_is_forward_only_and_terminal(store) -> None:
    m = send("agent:a", "session:1", "result", {"ok": True}, store=store, evidence_refs=["receipt:1"])

    assert deliver(m["id"], store=store)["delivery_state"] == "delivered"
    assert consume(m["id"], store=store)["delivery_state"] == "consumed"
    assert ack(m["id"], store=store)["delivery_state"] == "acknowledged"
    done = act(m["id"], store=store)
    assert done["delivery_state"] == "acted"
    assert done["acted_at"]

    # Terminal: acted cannot move further or backward.
    with pytest.raises(CoordinationError):
        cancel(m["id"], store=store)
    with pytest.raises(CoordinationError):
        deliver(m["id"], store=store)

    # Forward-only: delivered cannot go back to persisted (the service never exposes a
    # backwards transition, and a terminal state blocks everything).
    m2 = send("agent:b", "session:2", "inform", {}, store=store)
    deliver(m2["id"], store=store)
    assert list_inbox("session:2", store=store, states=("delivered",))[0]["id"] == m2["id"]


def test_cancel_is_terminal_for_a_persisted_message(store) -> None:
    m = send("agent:a", "session:1", "request", {}, store=store)
    cancelled = cancel(m["id"], store=store)
    assert cancelled["delivery_state"] == "cancelled"
    with pytest.raises(CoordinationError):
        deliver(m["id"], store=store)


def test_expire_moves_due_messages_to_terminal(store) -> None:
    m = send(
        "agent:a", "session:1", "question", {},
        store=store, expires_at="2020-01-01T00:00:00Z",
    )
    expired = expire(now="2026-01-01T00:00:00Z", store=store)
    assert [e["id"] for e in expired] == [m["id"]]
    assert expired[0]["delivery_state"] == "expired"


def test_inbox_requires_a_valid_recipient(store) -> None:
    with pytest.raises(CoordinationError):
        list_inbox("nope", store=store)
