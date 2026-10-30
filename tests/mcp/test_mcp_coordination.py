"""MCP coordination bundle — read-only mailbox inspection and counting."""

from __future__ import annotations

import pytest

from navig.coordination import deliver, send
from navig.mcp.tools.coordination import _inbox_handler, _stats_handler
from navig.store.coordination import CoordinationStore


@pytest.fixture
def store(tmp_path):  # noqa: ANN001
    return CoordinationStore(tmp_path / "coordination.db")


def test_inbox_handler_reads_a_participant(monkeypatch, store) -> None:
    monkeypatch.setattr("navig.coordination.get_coordination_store", lambda: store)
    send("agent:a", "session:1", "result", {"ok": True}, store=store)

    out = _inbox_handler(None, {"recipient": "session:1"})
    assert out["count"] == 1
    assert out["messages"][0]["kind"] == "result"
    assert out["messages"][0]["content"] == {"ok": True}


def test_inbox_handler_reports_an_invalid_recipient(monkeypatch, store) -> None:
    monkeypatch.setattr("navig.coordination.get_coordination_store", lambda: store)
    out = _inbox_handler(None, {"recipient": "bogus"})
    assert "error" in out


def test_stats_handler_counts_each_state(monkeypatch, store) -> None:
    monkeypatch.setattr("navig.coordination.get_coordination_store", lambda: store)
    first = send("agent:a", "session:1", "result", {"n": 1}, store=store)
    send("agent:a", "session:1", "inform", {"n": 2}, store=store)
    deliver(first["id"], store=store)

    out = _stats_handler(None, {"recipient": "session:1"})
    assert out["recipient"] == "session:1"
    assert out["total"] == 2
    assert sum(out["by_state"].values()) == out["total"]
    assert out["by_state"].get("delivered") == 1
    assert out["by_state"].get("persisted") == 1


def test_stats_handler_counts_only_its_recipient(monkeypatch, store) -> None:
    monkeypatch.setattr("navig.coordination.get_coordination_store", lambda: store)
    send("agent:a", "session:1", "result", {"n": 1}, store=store)
    send("agent:a", "session:2", "result", {"n": 2}, store=store)

    out = _stats_handler(None, {"recipient": "session:1"})
    assert out["total"] == 1


def test_stats_handler_reports_an_invalid_recipient(monkeypatch, store) -> None:
    monkeypatch.setattr("navig.coordination.get_coordination_store", lambda: store)
    out = _stats_handler(None, {"recipient": "bogus"})
    assert "error" in out
