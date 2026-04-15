"""Tests for navig.memory.session_memory — SessionMemoryExtractor."""

from __future__ import annotations

from pathlib import Path


class TestSessionMemoryExtractor:
    def test_record_tool_call_increments_counter(self, tmp_path):
        from navig.memory.session_memory import SessionMemoryExtractor

        ext = SessionMemoryExtractor(
            session_id="test-sess-1",
            interval=100,  # high interval so no extraction fires
            effort="low",
            notes_dir=tmp_path,
        )
        ext.record_tool_call()
        ext.record_tool_call()
        assert ext._tool_call_count == 2

    def test_load_notes_returns_none_when_no_file(self, tmp_path):
        from navig.memory.session_memory import SessionMemoryExtractor

        ext = SessionMemoryExtractor(
            session_id="no-file-session",
            interval=100,
            effort="low",
            notes_dir=tmp_path,
        )
        assert ext.load_notes() is None

    def test_load_notes_reads_existing_file(self, tmp_path):
        from navig.memory.session_memory import SessionMemoryExtractor

        ext = SessionMemoryExtractor(
            session_id="existing-session",
            interval=100,
            effort="low",
            notes_dir=tmp_path,
        )
        notes_path = tmp_path / "existing-session_notes.md"
        notes_path.write_text("## What was discussed\nUnit testing\n", encoding="utf-8")
        result = ext.load_notes()
        assert result is not None
        assert "Unit testing" in result

    def test_safe_session_id_strips_special_chars(self, tmp_path):
        from navig.memory.session_memory import SessionMemoryExtractor

        ext = SessionMemoryExtractor(
            session_id="abc/def:ghi",
            interval=100,
            effort="low",
            notes_dir=tmp_path,
        )
        # load_notes should not raise even with special chars in session_id
        assert ext.load_notes() is None


class TestRegistry:
    def test_same_session_returns_same_extractor(self):
        from navig.memory.session_memory import get_session_extractor

        a = get_session_extractor("regtest-1")
        b = get_session_extractor("regtest-1")
        assert a is b

    def test_different_sessions_give_different_extractors(self):
        from navig.memory.session_memory import get_session_extractor

        a = get_session_extractor("regtest-alpha")
        b = get_session_extractor("regtest-beta")
        assert a is not b
