"""Hermetic unit tests for navig.plans.inbox_reader."""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.plans.inbox_reader import (
    InboxItem,
    InboxReader,
    canonical_name,
    parse_suffix_state,
)

# ---------------------------------------------------------------------------
# parse_suffix_state
# ---------------------------------------------------------------------------


class TestParseSuffixState:
    def test_plain_md_is_active(self):
        assert parse_suffix_state("idea.md") == "active"

    def test_done_suffix(self):
        assert parse_suffix_state("task.md.done") == "done"

    def test_archive_suffix(self):
        assert parse_suffix_state("note.md.archive") == "archive"

    def test_review_suffix(self):
        assert parse_suffix_state("item.md.review") == "review"

    def test_uppercase_extension_is_active(self):
        # Checks case-insensitive matching (.MD.DONE)
        assert parse_suffix_state("FILE.MD.DONE") == "done"

    def test_mixed_case_archive(self):
        assert parse_suffix_state("FILE.MD.ARCHIVE") == "archive"

    def test_unknown_extension_is_active(self):
        assert parse_suffix_state("random.txt") == "active"

    def test_empty_string_is_active(self):
        assert parse_suffix_state("") == "active"


# ---------------------------------------------------------------------------
# canonical_name
# ---------------------------------------------------------------------------


class TestCanonicalName:
    def test_plain_md_unchanged(self):
        assert canonical_name("task.md") == "task.md"

    def test_strips_done(self):
        assert canonical_name("task.md.done") == "task.md"

    def test_strips_archive(self):
        assert canonical_name("idea.md.archive") == "idea.md"

    def test_strips_review(self):
        assert canonical_name("note.md.review") == "note.md"

    def test_uppercase_done(self):
        assert canonical_name("FILE.MD.DONE") == "FILE.MD"

    def test_no_double_strip(self):
        # Only strip the outermost suffix
        assert canonical_name("x.md.done.archive") == "x.md.done"


# ---------------------------------------------------------------------------
# InboxReader — helpers
# ---------------------------------------------------------------------------


class TestIsInboxFile:
    def _check(self, name: str) -> bool:
        return InboxReader._is_inbox_file(name)

    def test_plain_md(self):
        assert self._check("item.md") is True

    def test_md_done(self):
        assert self._check("item.md.done") is True

    def test_md_archive(self):
        assert self._check("item.md.archive") is True

    def test_md_review(self):
        assert self._check("item.md.review") is True

    def test_txt_false(self):
        assert self._check("item.txt") is False

    def test_json_false(self):
        assert self._check("config.json") is False

    def test_directory_name_false(self):
        assert self._check("not-a-file") is False


# ---------------------------------------------------------------------------
# InboxReader — class
# ---------------------------------------------------------------------------


_SIMPLE_FILE = "---\ntitle: test item\n---\n\nBody text here."
_NO_FRONTMATTER = "Just body text without frontmatter."


def _make_inbox(tmp_path: Path) -> Path:
    """Create .navig/inbox/ directory under tmp_path."""
    inbox = tmp_path / ".navig" / "inbox"
    inbox.mkdir(parents=True)
    return inbox


class TestInboxReaderScan:
    def test_empty_inbox_returns_empty_list(self, tmp_path):
        _make_inbox(tmp_path)
        reader = InboxReader(tmp_path)
        assert reader.scan() == []

    def test_missing_inbox_returns_empty_list(self, tmp_path):
        reader = InboxReader(tmp_path)
        assert reader.scan() == []

    def test_reads_active_items(self, tmp_path):
        inbox = _make_inbox(tmp_path)
        (inbox / "item.md").write_text(_SIMPLE_FILE, encoding="utf-8")
        reader = InboxReader(tmp_path)
        items = reader.scan()
        assert len(items) == 1
        assert items[0].name == "item.md"

    def test_skips_done_by_default(self, tmp_path):
        inbox = _make_inbox(tmp_path)
        (inbox / "item.md").write_text(_SIMPLE_FILE, encoding="utf-8")
        (inbox / "done.md.done").write_text(_SIMPLE_FILE, encoding="utf-8")
        reader = InboxReader(tmp_path)
        items = reader.scan()
        assert len(items) == 1
        assert items[0].name == "item.md"

    def test_includes_done_when_flag_set(self, tmp_path):
        inbox = _make_inbox(tmp_path)
        (inbox / "item.md").write_text(_SIMPLE_FILE, encoding="utf-8")
        (inbox / "done.md.done").write_text(_SIMPLE_FILE, encoding="utf-8")
        reader = InboxReader(tmp_path)
        items = reader.scan(include_done=True)
        assert len(items) == 2

    def test_item_suffix_state_active(self, tmp_path):
        inbox = _make_inbox(tmp_path)
        (inbox / "active.md").write_text(_SIMPLE_FILE, encoding="utf-8")
        items = InboxReader(tmp_path).scan()
        assert items[0].suffix_state == "active"

    def test_item_suffix_state_review(self, tmp_path):
        inbox = _make_inbox(tmp_path)
        (inbox / "pending.md.review").write_text(_SIMPLE_FILE, encoding="utf-8")
        items = InboxReader(tmp_path).scan(include_done=True)
        assert items[0].suffix_state == "review"

    def test_item_frontmatter_parsed(self, tmp_path):
        inbox = _make_inbox(tmp_path)
        (inbox / "item.md").write_text(_SIMPLE_FILE, encoding="utf-8")
        items = InboxReader(tmp_path).scan()
        assert items[0].frontmatter.get("title") == "test item"

    def test_item_body_stripped(self, tmp_path):
        inbox = _make_inbox(tmp_path)
        (inbox / "item.md").write_text(_SIMPLE_FILE, encoding="utf-8")
        items = InboxReader(tmp_path).scan()
        assert "Body text here." in items[0].body

    def test_skips_directories(self, tmp_path):
        inbox = _make_inbox(tmp_path)
        (inbox / "subdir").mkdir()
        (inbox / "item.md").write_text(_SIMPLE_FILE, encoding="utf-8")
        items = InboxReader(tmp_path).scan()
        assert len(items) == 1

    def test_items_sorted_by_name(self, tmp_path):
        inbox = _make_inbox(tmp_path)
        (inbox / "z_item.md").write_text(_SIMPLE_FILE, encoding="utf-8")
        (inbox / "a_item.md").write_text(_SIMPLE_FILE, encoding="utf-8")
        items = InboxReader(tmp_path).scan()
        assert items[0].name == "a_item.md"
        assert items[1].name == "z_item.md"

    def test_non_markdown_files_ignored(self, tmp_path):
        inbox = _make_inbox(tmp_path)
        (inbox / "config.json").write_text("{}", encoding="utf-8")
        (inbox / "item.md").write_text(_SIMPLE_FILE, encoding="utf-8")
        items = InboxReader(tmp_path).scan()
        assert len(items) == 1


class TestInboxReaderReadItem:
    def test_reads_existing_item(self, tmp_path):
        inbox = _make_inbox(tmp_path)
        (inbox / "item.md").write_text(_SIMPLE_FILE, encoding="utf-8")
        reader = InboxReader(tmp_path)
        item = reader.read_item("item.md")
        assert item is not None
        assert item.name == "item.md"

    def test_returns_none_for_missing(self, tmp_path):
        _make_inbox(tmp_path)
        reader = InboxReader(tmp_path)
        assert reader.read_item("nonexistent.md") is None

    def test_reads_done_item(self, tmp_path):
        inbox = _make_inbox(tmp_path)
        (inbox / "task.md.done").write_text(_SIMPLE_FILE, encoding="utf-8")
        reader = InboxReader(tmp_path)
        item = reader.read_item("task.md.done")
        assert item is not None
        assert item.suffix_state == "done"

    def test_inbox_dir_property(self, tmp_path):
        reader = InboxReader(tmp_path)
        assert reader.inbox_dir == (tmp_path / ".navig" / "inbox").resolve()
