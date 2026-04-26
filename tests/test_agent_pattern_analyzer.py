"""Tests for navig.agent.pattern_analyzer — PatternAnalyzer."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from navig.agent.pattern_analyzer import PatternAnalyzer, ScoredPattern


# ---------------------------------------------------------------------------
# ScoredPattern dataclass
# ---------------------------------------------------------------------------

class TestScoredPattern:
    def test_construction(self):
        sp = ScoredPattern(sequence=("ls", "-la"), occurrences=3, score=3.0)
        assert sp.sequence == ("ls", "-la")
        assert sp.occurrences == 3
        assert sp.score == 3.0


# ---------------------------------------------------------------------------
# PatternAnalyzer
# ---------------------------------------------------------------------------

@dataclass
class FakeRecord:
    command: str | None


class TestPatternAnalyzer:
    def test_empty_records_returns_empty(self):
        pa = PatternAnalyzer()
        assert pa.score_by_frequency([]) == []

    def test_records_without_command_attr_ignored(self):
        pa = PatternAnalyzer(min_occurrences=1)
        records = [object(), object()]
        assert pa.score_by_frequency(records) == []

    def test_none_command_ignored(self):
        pa = PatternAnalyzer(min_occurrences=1)
        records = [FakeRecord(command=None)]
        assert pa.score_by_frequency(records) == []

    def test_blank_command_ignored(self):
        pa = PatternAnalyzer(min_occurrences=1)
        records = [FakeRecord(command="   ")]
        assert pa.score_by_frequency(records) == []

    def test_single_occurrence_below_min_filtered(self):
        pa = PatternAnalyzer(min_occurrences=2)
        records = [FakeRecord(command="ls")]
        assert pa.score_by_frequency(records) == []

    def test_meets_min_occurrences(self):
        pa = PatternAnalyzer(min_occurrences=2)
        records = [FakeRecord(command="ls")] * 2
        result = pa.score_by_frequency(records)
        assert len(result) == 1
        assert result[0].sequence == ("ls",)
        assert result[0].occurrences == 2

    def test_score_equals_occurrences(self):
        pa = PatternAnalyzer(min_occurrences=1)
        records = [FakeRecord(command="apt")] * 5
        result = pa.score_by_frequency(records)
        assert result[0].score == 5.0

    def test_sorted_by_score_descending(self):
        pa = PatternAnalyzer(min_occurrences=1)
        records = (
            [FakeRecord(command="a")] * 3 +
            [FakeRecord(command="b")] * 7 +
            [FakeRecord(command="c")] * 1
        )
        result = pa.score_by_frequency(records)
        scores = [r.score for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_max_results_limits_output(self):
        pa = PatternAnalyzer(min_occurrences=1, max_results=3)
        commands = [f"cmd{i}" for i in range(10)]
        records = [FakeRecord(command=cmd) for cmd in commands for _ in range(2)]
        result = pa.score_by_frequency(records)
        assert len(result) <= 3

    def test_strips_whitespace(self):
        pa = PatternAnalyzer(min_occurrences=1)
        records = [FakeRecord(command="  ls  ")] * 2
        result = pa.score_by_frequency(records)
        assert result[0].sequence == ("ls",)

    def test_default_min_occurrences_is_2(self):
        pa = PatternAnalyzer()
        assert pa.min_occurrences == 2

    def test_default_max_results_is_20(self):
        pa = PatternAnalyzer()
        assert pa.max_results == 20
