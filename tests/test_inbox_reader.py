"""Tests for navig.plans.inbox_reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.plans.inbox_reader import (
    InboxItem,
    InboxReader,
    canonical_name,
    parse_suffix_state,
)


# ── Suffix state parsing ─────────────────────────────────────


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("idea.md", "active"),
        ("old.md.done", "done"),
        ("stale.md.archive", "archive"),
        ("unsure.md.review", "review"),
        ("UPPER.MD.DONE", "done"),
        ("mixed.Md.Archive", "archive"),
    ],
)
def test_parse_suffix_state(filename: str, expected: str) -> None:
    assert parse_suffix_state(filename) == expected


# ── Canonical name ────────────────────────────────────────────


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("task.md", "task.md"),
        ("task.md.done", "task.md"),
        ("task.md.archive", "task.md"),
        ("task.md.review", "task.md"),
    ],
)
def test_canonical_name(filename: str, expected: str) -> None:
    assert canonical_name(filename) == expected


# ── InboxReader ───────────────────────────────────────────────


@pytest.fixture()
def inbox_tree(tmp_path: Path) -> Path:
    """Create a minimal .navig/inbox/ tree for testing."""
    inbox = tmp_path / ".navig" / "inbox"
    inbox.mkdir(parents=True)

    # Active item with frontmatter
    (inbox / "active_task.md").write_text(
        "---\ntitle: My Active Task\ntype: task\n---\n\nDo something.\n",
        encoding="utf-8",
    )
    # Done item
    (inbox / "finished.md.done").write_text(
        "---\ntitle: Finished Task\n---\n\nAlready done.\n",
        encoding="utf-8",
    )
    # Review item
    (inbox / "uncertain.md.review").write_text(
        "---\ntitle: Needs Review\nreview_reason: duplicate\n---\n\nMaybe a dup.\n",
        encoding="utf-8",
    )
    # Non-markdown file (should be skipped)
    (inbox / "notes.txt").write_text("plain text", encoding="utf-8")

    return tmp_path


def test_scan_skips_done_by_default(inbox_tree: Path) -> None:
    reader = InboxReader(inbox_tree)
    items = reader.scan()
    names = [i.name for i in items]
    assert "active_task.md" in names
    assert "uncertain.md" in names
    assert "finished.md" not in names


def test_scan_includes_done_when_requested(inbox_tree: Path) -> None:
    reader = InboxReader(inbox_tree)
    items = reader.scan(include_done=True)
    names = [i.name for i in items]
    assert "finished.md" in names


def test_scan_skips_non_markdown(inbox_tree: Path) -> None:
    reader = InboxReader(inbox_tree)
    items = reader.scan(include_done=True)
    paths = [i.path.name for i in items]
    assert "notes.txt" not in paths


def test_frontmatter_parsed(inbox_tree: Path) -> None:
    reader = InboxReader(inbox_tree)
    items = reader.scan()
    active = [i for i in items if i.name == "active_task.md"][0]
    assert active.frontmatter["title"] == "My Active Task"
    assert active.frontmatter["type"] == "task"
    assert "Do something." in active.body


def test_read_item_by_name(inbox_tree: Path) -> None:
    reader = InboxReader(inbox_tree)
    item = reader.read_item("active_task.md")
    assert item is not None
    assert item.suffix_state == "active"


def test_read_item_missing(inbox_tree: Path) -> None:
    reader = InboxReader(inbox_tree)
    assert reader.read_item("nonexistent.md") is None


def test_scan_empty_dir(tmp_path: Path) -> None:
    reader = InboxReader(tmp_path)
    assert reader.scan() == []


def test_inbox_dir_property(tmp_path: Path) -> None:
    reader = InboxReader(tmp_path)
    assert reader.inbox_dir == (tmp_path / ".navig" / "inbox").resolve()
