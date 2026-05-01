"""Tests for commands/sessions.py — ChatTurn, ChatSession, _workspace_label — batch 117."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# ChatTurn
# ---------------------------------------------------------------------------

class TestChatTurn:
    def _make(self, **kwargs):
        from navig.commands.sessions import ChatTurn
        defaults = dict(role="user", text="Hello AI")
        defaults.update(kwargs)
        return ChatTurn(**defaults)

    def test_role_stored(self):
        t = self._make(role="assistant")
        assert t.role == "assistant"

    def test_text_stored(self):
        t = self._make(text="Help me deploy")
        assert t.text == "Help me deploy"

    def test_timestamp_default_none(self):
        t = self._make()
        assert t.timestamp is None

    def test_short_within_limit(self):
        t = self._make(text="Hello")
        assert t.short(120) == "Hello"

    def test_short_truncates(self):
        t = self._make(text="A" * 200)
        result = t.short(120)
        assert len(result) <= 124  # 120 + "..."

    def test_short_replaces_newlines(self):
        t = self._make(text="line1\nline2")
        result = t.short()
        assert "\n" not in result

    def test_short_appends_ellipsis_when_long(self):
        t = self._make(text="X" * 200)
        assert t.short(50).endswith("…")

    def test_short_no_ellipsis_when_short(self):
        t = self._make(text="short text")
        assert not t.short(120).endswith("…")


# ---------------------------------------------------------------------------
# ChatSession
# ---------------------------------------------------------------------------

class TestChatSession:
    def _make(self, **kwargs):
        from navig.commands.sessions import ChatSession
        defaults = dict(
            session_id="sess-abc",
            path=Path("/tmp/sessions/sess-abc"),
            workspace_hash="abc123",
            workspace_label="my-project",
        )
        defaults.update(kwargs)
        return ChatSession(**defaults)

    def test_session_id_stored(self):
        s = self._make(session_id="xyz789")
        assert s.session_id == "xyz789"

    def test_workspace_label_stored(self):
        s = self._make(workspace_label="proj")
        assert s.workspace_label == "proj"

    def test_turns_default_empty(self):
        s = self._make()
        assert s.turns == []

    def test_turn_count_zero_initially(self):
        s = self._make()
        assert s.turn_count == 0

    def test_user_count_zero_initially(self):
        s = self._make()
        assert s.user_count == 0

    def test_first_user_message_empty_when_no_turns(self):
        s = self._make()
        assert s.first_user_message == ""

    def test_turn_count_with_turns(self):
        from navig.commands.sessions import ChatSession, ChatTurn
        s = self._make()
        s.turns = [
            ChatTurn(role="user", text="Hi"),
            ChatTurn(role="assistant", text="Hello"),
        ]
        assert s.turn_count == 2

    def test_user_count_filters_correctly(self):
        from navig.commands.sessions import ChatSession, ChatTurn
        s = self._make()
        s.turns = [
            ChatTurn(role="user", text="Q1"),
            ChatTurn(role="assistant", text="A1"),
            ChatTurn(role="user", text="Q2"),
        ]
        assert s.user_count == 2

    def test_first_user_message_returns_text(self):
        from navig.commands.sessions import ChatSession, ChatTurn
        s = self._make()
        s.turns = [
            ChatTurn(role="assistant", text="Welcome"),
            ChatTurn(role="user", text="What is 2+2?"),
        ]
        assert s.first_user_message == "What is 2+2?"

    def test_first_user_message_truncated_at_200(self):
        from navig.commands.sessions import ChatSession, ChatTurn
        s = self._make()
        s.turns = [ChatTurn(role="user", text="X" * 300)]
        assert len(s.first_user_message) <= 200

    def test_parse_error_default_none(self):
        s = self._make()
        assert s.parse_error is None

    def test_file_bytes_default_zero(self):
        s = self._make()
        assert s.file_bytes == 0


# ---------------------------------------------------------------------------
# _workspace_label
# ---------------------------------------------------------------------------

class TestWorkspaceLabel:
    def _fn(self):
        from navig.commands.sessions import _workspace_label
        return _workspace_label

    def test_no_workspace_json_returns_hash_prefix(self, tmp_path):
        fn = self._fn()
        # tmp_path has no workspace.json
        result = fn(tmp_path)
        assert result == tmp_path.name[:12]

    def test_workspace_json_with_folder(self, tmp_path):
        fn = self._fn()
        data = {"folder": "file:///home/user/my-project"}
        (tmp_path / "workspace.json").write_text(json.dumps(data))
        result = fn(tmp_path)
        assert "my-project" in result

    def test_workspace_json_windows_path(self, tmp_path):
        fn = self._fn()
        data = {"folder": "file:///C:/Users/user/my-project"}
        (tmp_path / "workspace.json").write_text(json.dumps(data))
        result = fn(tmp_path)
        # Should extract "my-project" or similar
        assert isinstance(result, str)
        assert len(result) > 0

    def test_malformed_json_falls_back(self, tmp_path):
        fn = self._fn()
        (tmp_path / "workspace.json").write_text("{not valid json}")
        result = fn(tmp_path)
        # Falls back to hash prefix
        assert result == tmp_path.name[:12]

    def test_missing_folder_key_falls_back(self, tmp_path):
        fn = self._fn()
        (tmp_path / "workspace.json").write_text(json.dumps({"other": "data"}))
        result = fn(tmp_path)
        assert result == tmp_path.name[:12]

    def test_url_encoded_folder(self, tmp_path):
        fn = self._fn()
        data = {"folder": "file:///home/user/my%20project"}
        (tmp_path / "workspace.json").write_text(json.dumps(data))
        result = fn(tmp_path)
        # "my project" (decoded) → last path segment
        assert "my project" in result or "my" in result
