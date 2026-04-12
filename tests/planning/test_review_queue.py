"""Tests for navig.plans.review_queue."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.plans.review_queue import ReviewQueue

pytestmark = pytest.mark.integration


@pytest.fixture()
def review_tree(tmp_path: Path) -> Path:
    """Create .navig/ tree with review items in multiple directories."""
    inbox = tmp_path / ".navig" / "inbox"
    inbox.mkdir(parents=True)
    review_dir = tmp_path / ".navig" / "plans" / "tasks" / "review"
    review_dir.mkdir(parents=True)

    (inbox / "inbox_item.md.review").write_text(
        "---\ntitle: Inbox Review Item\nreview_reason: duplicate\n---\n\nPossible dup.\n",
        encoding="utf-8",
    )
    (review_dir / "task_item.md.review").write_text(
        "---\ntitle: Task Review Item\nreview_reason: conflict\n---\n\nConflicting task.\n",
        encoding="utf-8",
    )
    # Non-review file (should be skipped)
    (inbox / "active.md").write_text(
        "---\ntitle: Active Item\n---\n\nNormal item.\n",
        encoding="utf-8",
    )

    return tmp_path


def test_list_items(review_tree: Path) -> None:
    queue = ReviewQueue(review_tree)
    items = queue.list_items()
    names = [i.name for i in items]
    assert "inbox_item.md" in names
    assert "task_item.md" in names
    assert len(items) == 2


def test_list_items_excludes_active(review_tree: Path) -> None:
    queue = ReviewQueue(review_tree)
    items = queue.list_items()
    names = [i.name for i in items]
    assert "active.md" not in names


def test_get_item_detail(review_tree: Path) -> None:
    queue = ReviewQueue(review_tree)
    item = queue.get_item_detail("inbox_item.md.review")
    assert item is not None
    assert item.title == "Inbox Review Item"
    assert item.reason == "duplicate"


def test_get_item_detail_not_found(review_tree: Path) -> None:
    queue = ReviewQueue(review_tree)
    assert queue.get_item_detail("nonexistent.md.review") is None


def test_archive_item(review_tree: Path) -> None:
    queue = ReviewQueue(review_tree)
    assert queue.archive_item("inbox_item.md.review") is True

    inbox = review_tree / ".navig" / "inbox"
    assert not (inbox / "inbox_item.md.review").exists()
    assert (inbox / "inbox_item.md.archive").exists()


def test_archive_item_not_found(review_tree: Path) -> None:
    queue = ReviewQueue(review_tree)
    assert queue.archive_item("missing.md.review") is False


def test_commit_item(review_tree: Path) -> None:
    queue = ReviewQueue(review_tree)
    # Commit moves review item back to inbox as active .md
    assert queue.commit_item("task_item.md.review") is True

    inbox = review_tree / ".navig" / "inbox"
    assert (inbox / "task_item.md").exists()
    review_dir = review_tree / ".navig" / "plans" / "tasks" / "review"
    assert not (review_dir / "task_item.md.review").exists()


def test_commit_item_not_found(review_tree: Path) -> None:
    queue = ReviewQueue(review_tree)
    assert queue.commit_item("ghost.md.review") is False


def test_empty_queue(tmp_path: Path) -> None:
    queue = ReviewQueue(tmp_path)
    assert queue.list_items() == []
