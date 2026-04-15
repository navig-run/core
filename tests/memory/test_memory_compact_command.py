from __future__ import annotations

from dataclasses import dataclass

import pytest

from navig.commands.memory import memory_compact

pytestmark = pytest.mark.integration


@dataclass
class _Session:
    session_key: str
    message_count: int


class _FakeConfig:
    def __init__(self, global_config_dir: str):
        self.global_config_dir = global_config_dir

    def get(self, key: str, default=None):
        if key == "memory.compact_threshold_messages":
            return 20
        if key == "memory.compact_summary_effort":
            return "low"
        return default


class _FakeStore:
    def __init__(self, *_args, **_kwargs):
        self.closed = False

    def get_session(self, session_key: str):
        if session_key == "sess-1":
            return _Session(session_key=session_key, message_count=25)
        return None

    def list_sessions(self, limit=1):
        return [_Session(session_key="sess-1", message_count=25)]

    def get_history(self, session_key: str, limit: int = 0):
        return []

    def close(self):
        self.closed = True


def test_memory_compact_plain_still_requires_confirmation(monkeypatch, tmp_path):
    """`--plain` should not bypass destructive confirmation; only --yes can."""
    cfg_dir = tmp_path / ".navig"
    db_path = cfg_dir / "memory" / "memory.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_text("", encoding="utf-8")

    confirm_called = {"value": False}

    def _fake_confirm(_prompt: str):
        confirm_called["value"] = True
        return False

    monkeypatch.setattr("navig.commands.memory._get_config", lambda: _FakeConfig(str(cfg_dir)))
    monkeypatch.setattr("navig.memory.ConversationStore", _FakeStore)
    monkeypatch.setattr("typer.confirm", _fake_confirm)

    # Should ask for confirmation and then abort cleanly.
    memory_compact(session="sess-1", plain=True, yes=False)
    assert confirm_called["value"] is True
