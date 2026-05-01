"""
Batch 80: navig/agent/memory.py, navig/agent/pattern_observer.py,
          navig/agent/pattern_analyzer.py
"""
from __future__ import annotations

import sqlite3
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# agent/memory.py
# ---------------------------------------------------------------------------
from navig.agent.memory import get_memory, _LegacyMemoryAdapter


class TestLegacyMemoryAdapter:
    def test_get_memory_returns_adapter(self):
        m = get_memory()
        assert isinstance(m, _LegacyMemoryAdapter)

    def test_get_recent_returns_empty_list(self):
        m = get_memory()
        result = m.get_recent("user_123")
        assert result == []

    def test_get_recent_with_limit(self):
        m = get_memory()
        result = m.get_recent("user_abc", limit=5)
        assert isinstance(result, list)
        assert len(result) == 0

    def test_multiple_calls_return_empty(self):
        m = get_memory()
        assert m.get_recent("a") == []
        assert m.get_recent("b") == []


# ---------------------------------------------------------------------------
# agent/pattern_observer.py
# ---------------------------------------------------------------------------
from navig.agent.pattern_observer import PatternObserver, PatternRecord


class TestPatternObserver:
    def test_empty_when_db_missing(self, tmp_path):
        obs = PatternObserver(db_path=tmp_path / "nonexistent.sqlite")
        assert obs.get_recent() == []

    def test_get_recent_from_populated_db(self, tmp_path):
        db_path = tmp_path / "patterns.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE patterns (command TEXT, ts REAL DEFAULT 0)")
        conn.execute("INSERT INTO patterns (command) VALUES ('ls -la')")
        conn.execute("INSERT INTO patterns (command) VALUES ('git status')")
        conn.commit()
        conn.close()

        obs = PatternObserver(db_path=db_path)
        results = obs.get_recent(limit=10)
        assert len(results) == 2
        commands = [r.command for r in results]
        assert "ls -la" in commands
        assert "git status" in commands

    def test_limit_restricts_results(self, tmp_path):
        db_path = tmp_path / "patterns.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE patterns (command TEXT, ts REAL DEFAULT 0)")
        for i in range(10):
            conn.execute(f"INSERT INTO patterns (command) VALUES ('cmd{i}')")
        conn.commit()
        conn.close()

        obs = PatternObserver(db_path=db_path)
        results = obs.get_recent(limit=3)
        assert len(results) == 3

    def test_returns_pattern_records(self, tmp_path):
        db_path = tmp_path / "patterns.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE patterns (command TEXT, ts REAL DEFAULT 0)")
        conn.execute("INSERT INTO patterns (command) VALUES ('pwd')")
        conn.commit()
        conn.close()

        obs = PatternObserver(db_path=db_path)
        results = obs.get_recent()
        assert all(isinstance(r, PatternRecord) for r in results)

    def test_null_command_rows_skipped(self, tmp_path):
        db_path = tmp_path / "patterns.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE patterns (command TEXT, ts REAL DEFAULT 0)")
        conn.execute("INSERT INTO patterns (command) VALUES (NULL)")
        conn.execute("INSERT INTO patterns (command) VALUES ('valid')")
        conn.commit()
        conn.close()

        obs = PatternObserver(db_path=db_path)
        results = obs.get_recent()
        assert len(results) == 1
        assert results[0].command == "valid"

    def test_bad_db_returns_empty(self, tmp_path):
        db_path = tmp_path / "corrupt.sqlite"
        db_path.write_bytes(b"not a real db")

        obs = PatternObserver(db_path=db_path)
        # Should swallow the error and return empty
        results = obs.get_recent()
        assert results == []


# ---------------------------------------------------------------------------
# agent/pattern_analyzer.py
# ---------------------------------------------------------------------------
from navig.agent.pattern_analyzer import PatternAnalyzer, ScoredPattern


class TestPatternAnalyzer:
    def test_empty_input(self):
        pa = PatternAnalyzer()
        assert pa.score_by_frequency([]) == []

    def test_single_occurrence_filtered(self):
        pa = PatternAnalyzer(min_occurrences=2)
        records = [type("R", (), {"command": "ls -la"})()]
        result = pa.score_by_frequency(records)
        assert result == []

    def test_frequent_command_included(self):
        pa = PatternAnalyzer(min_occurrences=2)
        cmd = type("R", (), {"command": "git status"})
        records = [cmd(), cmd(), cmd()]
        result = pa.score_by_frequency(records)
        assert len(result) == 1
        assert result[0].sequence == ("git status",)
        assert result[0].occurrences == 3

    def test_sorted_by_score_descending(self):
        pa = PatternAnalyzer(min_occurrences=2)
        cmd_a = type("R", (), {"command": "ls"})
        cmd_b = type("R", (), {"command": "git status"})
        records = [cmd_a(), cmd_a(), cmd_b(), cmd_b(), cmd_b()]
        result = pa.score_by_frequency(records)
        assert result[0].occurrences >= result[-1].occurrences

    def test_max_results_limit(self):
        pa = PatternAnalyzer(min_occurrences=1, max_results=3)
        records = [type("R", (), {"command": f"cmd{i}"})() for i in range(10)]
        result = pa.score_by_frequency(records)
        assert len(result) <= 3

    def test_skips_non_string_command(self):
        pa = PatternAnalyzer(min_occurrences=2)
        records = [
            type("R", (), {"command": 42})(),
            type("R", (), {"command": None})(),
            type("R", (), {"command": "valid"})(),
            type("R", (), {"command": "valid"})(),
        ]
        result = pa.score_by_frequency(records)
        assert len(result) == 1
        assert result[0].sequence == ("valid",)

    def test_skips_whitespace_only_command(self):
        pa = PatternAnalyzer(min_occurrences=2)
        records = [
            type("R", (), {"command": "   "})(),
            type("R", (), {"command": "   "})(),
        ]
        result = pa.score_by_frequency(records)
        assert result == []

    def test_skips_records_without_command_attr(self):
        pa = PatternAnalyzer(min_occurrences=2)
        records = [object(), object()]
        result = pa.score_by_frequency(records)
        assert result == []

    def test_scored_pattern_dataclass(self):
        sp = ScoredPattern(sequence=("a", "b"), occurrences=3, score=3.0)
        assert sp.sequence == ("a", "b")
        assert sp.occurrences == 3
        assert sp.score == 3.0
