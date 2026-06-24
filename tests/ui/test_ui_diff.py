"""Tests for navig.ui.diff — diff_lines_from_text and DiffLine semantics."""

from __future__ import annotations

from navig.ui.diff import diff_lines_from_text
from navig.ui.models import DiffLine, DiffPreview


# ──────────────────────────────────────────────────────────────
# diff_lines_from_text
# ──────────────────────────────────────────────────────────────


class TestDiffLinesFromText:
    def test_identical_texts_return_no_diff(self):
        lines = diff_lines_from_text("hello\nworld", "hello\nworld")
        # No additions or removals for identical texts
        ops = [l.op for l in lines]
        assert "add" not in ops
        assert "remove" not in ops

    def test_returns_list_of_diffline(self):
        lines = diff_lines_from_text("a", "b")
        assert all(isinstance(l, DiffLine) for l in lines)

    def test_detects_addition(self):
        lines = diff_lines_from_text("line1", "line1\nline2")
        ops = [l.op for l in lines]
        assert "add" in ops

    def test_detects_removal(self):
        lines = diff_lines_from_text("line1\nline2", "line1")
        ops = [l.op for l in lines]
        assert "remove" in ops

    def test_added_content_present(self):
        lines = diff_lines_from_text("original", "original\nnew line")
        added = [l.content for l in lines if l.op == "add"]
        assert any("new line" in c for c in added)

    def test_removed_content_present(self):
        lines = diff_lines_from_text("old line\nkeep", "keep")
        removed = [l.content for l in lines if l.op == "remove"]
        assert any("old line" in c for c in removed)

    def test_empty_inputs(self):
        lines = diff_lines_from_text("", "")
        assert lines == []

    def test_from_empty_to_content(self):
        lines = diff_lines_from_text("", "hello")
        ops = [l.op for l in lines]
        assert "add" in ops

    def test_from_content_to_empty(self):
        lines = diff_lines_from_text("hello", "")
        ops = [l.op for l in lines]
        assert "remove" in ops

    def test_multiline_diff(self):
        before = "a\nb\nc"
        after = "a\nx\nc"
        lines = diff_lines_from_text(before, after)
        ops = [l.op for l in lines]
        assert "add" in ops
        assert "remove" in ops

    def test_context_lines_present_for_nearby_changes(self):
        # unified_diff emits context lines around changes
        before = "1\n2\n3\n4\n5"
        after = "1\n2\nX\n4\n5"
        lines = diff_lines_from_text(before, after)
        ops = [l.op for l in lines]
        assert "context" in ops


# ──────────────────────────────────────────────────────────────
# DiffLine and DiffPreview models
# ──────────────────────────────────────────────────────────────


class TestDiffLine:
    def test_add_op(self):
        d = DiffLine(op="add", content="new line")
        assert d.op == "add"
        assert d.content == "new line"

    def test_remove_op(self):
        d = DiffLine(op="remove", content="old line")
        assert d.op == "remove"

    def test_context_op(self):
        d = DiffLine(op="context", content="ctx")
        assert d.op == "context"


class TestDiffPreview:
    def test_creates_with_lines(self):
        lines = [DiffLine(op="add", content="hello")]
        preview = DiffPreview(title="My Diff", lines=lines)
        assert preview.title == "My Diff"
        assert len(preview.lines) == 1
