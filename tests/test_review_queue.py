"""Hermetic unit tests for navig.plans.review_queue."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from navig.plans.review_queue import (
    ReviewItem,
    ReviewQueue,
    _canonical_name,
)

# ---------------------------------------------------------------------------
# _canonical_name
# ---------------------------------------------------------------------------


class TestCanonicalName:
    def test_strips_review_suffix(self):
        assert _canonical_name("task.md.review") == "task.md"

    def test_strips_done_suffix(self):
        assert _canonical_name("task.md.DONE") == "task.md"

    def test_strips_archive_suffix(self):
        assert _canonical_name("task.md.archive") == "task.md"

    def test_no_suffix_unchanged(self):
        assert _canonical_name("task.md") == "task.md"

    def test_case_insensitive_review(self):
        assert _canonical_name("task.md.REVIEW") == "task.md"

    def test_unrelated_suffix_preserved(self):
        assert _canonical_name("task.md.bak") == "task.md.bak"

    def test_empty_string(self):
        assert _canonical_name("") == ""

    def test_review_suffix_only(self):
        assert _canonical_name(".md.review") == ".md"


# ---------------------------------------------------------------------------
# ReviewItem dataclass
# ---------------------------------------------------------------------------


class TestReviewItemDataclass:
    def _make(self) -> ReviewItem:
        return ReviewItem(
            path=Path("/tmp/fake.md.review"),
            name="fake.md",
            title="Fake Task",
            frontmatter={"title": "Fake Task", "review_reason": "conflict"},
            body="Some body text.",
            reason="conflict",
        )

    def test_path(self):
        item = self._make()
        assert item.path == Path("/tmp/fake.md.review")

    def test_name(self):
        assert self._make().name == "fake.md"

    def test_title(self):
        assert self._make().title == "Fake Task"

    def test_reason(self):
        assert self._make().reason == "conflict"

    def test_body(self):
        assert self._make().body == "Some body text."

    def test_frozen(self):
        item = self._make()
        with pytest.raises((AttributeError, TypeError)):
            item.name = "other.md"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ReviewQueue.list_items
# ---------------------------------------------------------------------------


def _write_review_file(directory: Path, filename: str, content: str = "") -> Path:
    """Helper: write a .md.review file in *directory*."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    if not content:
        content = textwrap.dedent("""\
            ---
            title: Test Task
            review_reason: duplicate
            ---
            Body content here.
        """)
    path.write_text(content, encoding="utf-8")
    return path


class TestReviewQueueListItems:
    def test_empty_dirs_returns_empty_list(self, tmp_path):
        rq = ReviewQueue(tmp_path)
        assert rq.list_items() == []

    def test_finds_review_files_in_inbox(self, tmp_path):
        inbox = tmp_path / ".navig" / "inbox"
        _write_review_file(inbox, "task_one.md.review")
        rq = ReviewQueue(tmp_path)
        items = rq.list_items()
        assert len(items) == 1
        assert items[0].name == "task_one.md"

    def test_skips_non_review_files(self, tmp_path):
        inbox = tmp_path / ".navig" / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "active.md").write_text("active", encoding="utf-8")
        (inbox / "done.md.done").write_text("done", encoding="utf-8")
        rq = ReviewQueue(tmp_path)
        assert rq.list_items() == []

    def test_finds_review_files_in_tasks_review(self, tmp_path):
        review_dir = tmp_path / ".navig" / "plans" / "tasks" / "review"
        _write_review_file(review_dir, "conflict.md.review")
        rq = ReviewQueue(tmp_path)
        items = rq.list_items()
        assert len(items) == 1

    def test_multiple_items_sorted_by_name(self, tmp_path):
        inbox = tmp_path / ".navig" / "inbox"
        _write_review_file(inbox, "z_task.md.review")
        _write_review_file(inbox, "a_task.md.review")
        rq = ReviewQueue(tmp_path)
        items = rq.list_items()
        assert len(items) == 2
        assert items[0].name == "a_task.md"
        assert items[1].name == "z_task.md"

    def test_title_from_frontmatter(self, tmp_path):
        inbox = tmp_path / ".navig" / "inbox"
        content = "---\ntitle: Custom Title\nreview_reason: conflict\n---\nbody"
        _write_review_file(inbox, "task.md.review", content)
        rq = ReviewQueue(tmp_path)
        items = rq.list_items()
        assert items[0].title == "Custom Title"

    def test_reason_from_frontmatter(self, tmp_path):
        inbox = tmp_path / ".navig" / "inbox"
        content = "---\ntitle: T\nreview_reason: pipeline_error\n---\nbody"
        _write_review_file(inbox, "task.md.review", content)
        rq = ReviewQueue(tmp_path)
        items = rq.list_items()
        assert items[0].reason == "pipeline_error"

    def test_title_derived_from_filename_when_missing(self, tmp_path):
        inbox = tmp_path / ".navig" / "inbox"
        _write_review_file(inbox, "my_task.md.review", "no frontmatter content")
        rq = ReviewQueue(tmp_path)
        items = rq.list_items()
        # Title derived: "my_task" → "my task"
        assert "my" in items[0].title.lower()


# ---------------------------------------------------------------------------
# ReviewQueue.get_item_detail
# ---------------------------------------------------------------------------


class TestGetItemDetail:
    def test_found(self, tmp_path):
        inbox = tmp_path / ".navig" / "inbox"
        _write_review_file(inbox, "detail.md.review")
        rq = ReviewQueue(tmp_path)
        item = rq.get_item_detail("detail.md.review")
        assert item is not None
        assert item.name == "detail.md"

    def test_not_found_returns_none(self, tmp_path):
        rq = ReviewQueue(tmp_path)
        assert rq.get_item_detail("nonexistent.md.review") is None


# ---------------------------------------------------------------------------
# ReviewQueue.archive_item
# ---------------------------------------------------------------------------


class TestArchiveItem:
    def test_archive_renames_file(self, tmp_path):
        inbox = tmp_path / ".navig" / "inbox"
        path = _write_review_file(inbox, "task.md.review")
        rq = ReviewQueue(tmp_path)
        result = rq.archive_item("task.md.review")
        assert result is True
        assert not path.exists()
        assert (inbox / "task.md.archive").exists()

    def test_archive_missing_returns_false(self, tmp_path):
        rq = ReviewQueue(tmp_path)
        assert rq.archive_item("ghost.md.review") is False


# ---------------------------------------------------------------------------
# ReviewQueue.commit_item
# ---------------------------------------------------------------------------


class TestCommitItem:
    def test_commit_moves_to_inbox(self, tmp_path):
        review_dir = tmp_path / ".navig" / "plans" / "tasks" / "review"
        path = _write_review_file(review_dir, "active.md.review")
        rq = ReviewQueue(tmp_path)
        result = rq.commit_item("active.md.review")
        assert result is True
        assert not path.exists()
        assert (tmp_path / ".navig" / "inbox" / "active.md").exists()

    def test_commit_missing_returns_false(self, tmp_path):
        rq = ReviewQueue(tmp_path)
        assert rq.commit_item("ghost.md.review") is False
