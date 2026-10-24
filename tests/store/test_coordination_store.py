"""CoordinationStore — durable mailbox, idempotency and delivery-state persistence."""

from __future__ import annotations

import pytest

from navig.store.coordination import DELIVERY_STATES, CoordinationStore


@pytest.fixture
def store(tmp_path):  # noqa: ANN001
    return CoordinationStore(tmp_path / "coordination.db")


def _msg(**over):  # noqa: ANN003
    base = {
        "sender": "agent:a",
        "recipient": "session:1",
        "kind": "inform",
        "content": {"note": "hello"},
    }
    base.update(over)
    return base


def test_delivery_states_include_terminal_dead_letter() -> None:
    assert "persisted" in DELIVERY_STATES
    assert {"cancelled", "failed", "expired"} <= set(DELIVERY_STATES)


def test_put_persists_and_idempotency_key_dedupes(store) -> None:
    row, created = store.put(_msg(idempotency_key="k1"))
    assert created is True
    assert row["delivery_state"] == "persisted"
    assert row["content"] == {"note": "hello"}

    again, created_again = store.put(_msg(id="other-id", idempotency_key="k1"))
    assert created_again is False
    assert again["id"] == row["id"]


def test_inbox_filters_by_recipient_and_state(store) -> None:
    store.put(_msg(idempotency_key="a"))
    store.put(_msg(recipient="session:2", idempotency_key="b"))

    inbox = store.list_inbox("session:1")
    assert [m["recipient"] for m in inbox] == ["session:1"]

    store.set_delivery_state(inbox[0]["id"], "consumed")
    assert store.list_inbox("session:1", states=("persisted",)) == []


def test_state_transition_stamps_the_column(store) -> None:
    row, _ = store.put(_msg(idempotency_key="a"))
    updated = store.set_delivery_state(row["id"], "consumed")
    assert updated is not None
    assert updated["delivery_state"] == "consumed"
    assert updated["consumed_at"]


def test_expiry_query_returns_only_due_messages(store) -> None:
    store.put(_msg(idempotency_key="expired", expires_at="2020-01-01T00:00:00Z"))
    store.put(_msg(recipient="session:2", idempotency_key="alive", expires_at="2099-01-01T00:00:00Z"))

    expired = store.list_expired(now="2026-01-01T00:00:00Z")
    assert [e["idempotency_key"] for e in expired] == ["expired"]


def test_messages_survive_a_store_reopen(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "coordination.db"
    first = CoordinationStore(path)
    row, _ = first.put(_msg(idempotency_key="k-reopen"))

    reopened = CoordinationStore(path)
    assert reopened.get(row["id"])["delivery_state"] == "persisted"
    assert reopened.get_by_key("k-reopen")["id"] == row["id"]


def test_list_by_state_scans_all_recipients_oldest_first(store) -> None:
    store.put(_msg(idempotency_key="a"))
    store.put(_msg(recipient="session:2", idempotency_key="b"))
    rows = store.list_by_state(("persisted",))
    assert len(rows) == 2
    assert rows[0]["created_at"] <= rows[1]["created_at"]
