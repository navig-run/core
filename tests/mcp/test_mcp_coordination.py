"""MCP coordination bundle — read-only mailbox inspection."""

from __future__ import annotations

import pytest

from navig.coordination import send
from navig.mcp.tools.coordination import _inbox_handler
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
