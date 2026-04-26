"""Hermetic unit tests for navig.plans.corpus_scanner."""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.plans.corpus_scanner import (
    ConflictMatch,
    CorpusScanner,
    DuplicateMatch,
    _extract_sentences,
    _extract_title,
    _is_contradiction,
    _read_as_item,
)

# ---------------------------------------------------------------------------
# _extract_sentences
# ---------------------------------------------------------------------------


class TestExtractSentences:
    def test_splits_on_period(self):
        result = _extract_sentences("First sentence. Second sentence.")
        assert len(result) == 2

    def test_splits_on_newline(self):
        result = _extract_sentences("This is line one here\nThis is line two here")
        assert len(result) == 2

    def test_strips_whitespace(self):
        result = _extract_sentences("  hello world  . ")
        assert result[0] == "hello world"

    def test_short_chunks_excluded(self):
        # Chunks <= 10 chars are excluded
        result = _extract_sentences("Hi. This is a longer sentence here.")
        assert all(len(s) > 10 for s in result)

    def test_lowercases_output(self):
        result = _extract_sentences("This Is Mixed Case text here.")
        assert all(s == s.lower() for s in result)

    def test_empty_text_returns_empty(self):
        assert _extract_sentences("") == []

    def test_splits_on_exclamation(self):
        result = _extract_sentences("Great result! Another point here.")
        assert len(result) == 2

    def test_splits_on_question_mark(self):
        result = _extract_sentences("What is happening? This is the answer here.")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _is_contradiction
# ---------------------------------------------------------------------------


class TestIsContradiction:
    def test_negation_prefix_matches(self):
        assert _is_contradiction("not use docker for deployment", "use docker for deployment") is True

    def test_no_prefix_returns_false(self):
        assert _is_contradiction("use docker for deployment", "use kubernetes instead here") is False

    def test_never_prefix(self):
        assert _is_contradiction("never use plaintext passwords here", "use plaintext passwords here") is True

    def test_no_prefix_returns_false_symmetrically(self):
        assert _is_contradiction("a b c d e", "a b c d e") is False

    def test_core_too_short_returns_false(self):
        # Core must be >= 5 chars after stripping the prefix
        assert _is_contradiction("not x", "x") is False

    def test_symmetric_b_negates_a(self):
        # b starts with "no "
        assert _is_contradiction("store in plaintext database here", "no store in plaintext database here") is True

    def test_dont_prefix(self):
        assert _is_contradiction("don't expose internal api endpoints", "expose internal api endpoints here") is True


# ---------------------------------------------------------------------------
# _extract_title
# ---------------------------------------------------------------------------


class TestExtractTitle:
    def test_uses_frontmatter_title(self, tmp_path):
        f = tmp_path / "task.md"
        f.write_text("---\ntitle: My Task Title\n---\n\nBody.", encoding="utf-8")
        item = _read_as_item(f)
        assert item is not None
        assert _extract_title(item) == "my task title"

    def test_falls_back_to_filename(self, tmp_path):
        f = tmp_path / "deploy_service.md"
        f.write_text("No frontmatter here.", encoding="utf-8")
        item = _read_as_item(f)
        assert item is not None
        title = _extract_title(item)
        assert "deploy service" in title

    def test_title_is_lowercased(self, tmp_path):
        f = tmp_path / "MyFile.md"
        f.write_text("---\ntitle: UPPER CASE TITLE\n---\n", encoding="utf-8")
        item = _read_as_item(f)
        assert _extract_title(item) == "upper case title"


# ---------------------------------------------------------------------------
# _read_as_item
# ---------------------------------------------------------------------------


class TestReadAsItem:
    def test_reads_valid_file(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("---\ntitle: hello\n---\n\nBody.", encoding="utf-8")
        item = _read_as_item(f)
        assert item is not None
        assert item.frontmatter.get("title") == "hello"
        assert "Body." in item.body

    def test_returns_none_for_missing_file(self, tmp_path):
        f = tmp_path / "nonexistent.md"
        assert _read_as_item(f) is None


# ---------------------------------------------------------------------------
# DuplicateMatch / ConflictMatch dataclasses
# ---------------------------------------------------------------------------


class TestMatchDataclasses:
    def test_duplicate_match_fields(self, tmp_path):
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        match = DuplicateMatch(file_a=a, file_b=b, reason="title overlap")
        assert match.file_a == a
        assert match.file_b == b
        assert match.reason == "title overlap"

    def test_conflict_match_fields(self, tmp_path):
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        match = ConflictMatch(file_a=a, file_b=b, reason="negation detected")
        assert match.reason == "negation detected"

    def test_duplicate_match_frozen(self, tmp_path):
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        match = DuplicateMatch(file_a=a, file_b=b, reason="r")
        with pytest.raises((AttributeError, TypeError)):
            match.reason = "modified"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CorpusScanner integration
# ---------------------------------------------------------------------------


def _make_corpus(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create .navig/plans/tasks/active and decisions directories."""
    active = tmp_path / ".navig" / "plans" / "tasks" / "active"
    decisions = tmp_path / ".navig" / "plans" / "decisions"
    inbox = tmp_path / ".navig" / "inbox"
    active.mkdir(parents=True)
    decisions.mkdir(parents=True)
    inbox.mkdir(parents=True)
    return active, decisions, inbox


class TestCorpusScannerEmpty:
    def test_no_files_no_duplicates(self, tmp_path):
        _make_corpus(tmp_path)
        scanner = CorpusScanner(tmp_path)
        assert scanner.scan_for_duplicates() == []

    def test_no_files_no_conflicts(self, tmp_path):
        _make_corpus(tmp_path)
        scanner = CorpusScanner(tmp_path)
        assert scanner.scan_for_conflicts() == []

    def test_missing_dirs_returns_empty(self, tmp_path):
        scanner = CorpusScanner(tmp_path)
        dupes, conflicts = scanner.full_scan()
        assert dupes == []
        assert conflicts == []


class TestCorpusScannerDuplicates:
    def test_detects_duplicate_by_title(self, tmp_path):
        active, _, _ = _make_corpus(tmp_path)
        (active / "deploy_app.md").write_text(
            "---\ntitle: deploy app to production server\n---\nBody.", encoding="utf-8"
        )
        (active / "deploy_app_v2.md").write_text(
            "---\ntitle: deploy app to production server\n---\nBody.", encoding="utf-8"
        )
        scanner = CorpusScanner(tmp_path)
        dupes = scanner.scan_for_duplicates()
        assert len(dupes) == 1
        assert "deploy app" in dupes[0].reason

    def test_no_duplicate_when_different_titles(self, tmp_path):
        active, _, _ = _make_corpus(tmp_path)
        (active / "task_a.md").write_text(
            "---\ntitle: alpha feature work\n---\nBody.", encoding="utf-8"
        )
        (active / "task_b.md").write_text(
            "---\ntitle: beta testing phase\n---\nBody.", encoding="utf-8"
        )
        scanner = CorpusScanner(tmp_path)
        assert scanner.scan_for_duplicates() == []

    def test_single_file_no_duplicates(self, tmp_path):
        active, _, _ = _make_corpus(tmp_path)
        (active / "only_task.md").write_text(
            "---\ntitle: only task in system\n---\nBody.", encoding="utf-8"
        )
        scanner = CorpusScanner(tmp_path)
        assert scanner.scan_for_duplicates() == []


class TestCorpusScannerConflicts:
    def test_detects_conflict_by_negation(self, tmp_path):
        active, decisions, _ = _make_corpus(tmp_path)
        (decisions / "decision_a.md").write_text(
            "Use docker for all deployments always.\n", encoding="utf-8"
        )
        (decisions / "decision_b.md").write_text(
            "Not use docker for all deployments always.\n", encoding="utf-8"
        )
        scanner = CorpusScanner(tmp_path)
        conflicts = scanner.scan_for_conflicts()
        assert len(conflicts) == 1

    def test_full_scan_returns_tuple(self, tmp_path):
        _make_corpus(tmp_path)
        scanner = CorpusScanner(tmp_path)
        result = scanner.full_scan()
        assert isinstance(result, tuple)
        assert len(result) == 2
