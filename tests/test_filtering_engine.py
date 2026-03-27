"""
TDD tests for FilteringEngine.

Written BEFORE implementation — defines expected contract.
Run: pytest tests/test_filtering_engine.py -v
"""

from __future__ import annotations

import textwrap
import threading
import time
from pathlib import Path

import pytest

from navig.agents.filtering_engine import (
    FilteringEngine,
    FilterResult,
    NormalizationRule,
    apply_frontmatter,
    normalize_headings,
)

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────


def make_navig_tree(tmp_path: Path) -> Path:
    """Create a minimal .navig/ directory tree under tmp_path."""
    root = tmp_path / "project"
    (root / ".navig" / "plans").mkdir(parents=True)
    (root / ".navig" / "plans" / "briefs").mkdir()
    (root / ".navig" / "wiki").mkdir()
    (root / ".navig" / "memory").mkdir()
    return root


# ─────────────────────────────────────────────────────────────
# Unit tests — normalization helpers
# ─────────────────────────────────────────────────────────────


class TestApplyFrontmatter:
    """apply_frontmatter() should inject YAML frontmatter when missing."""

    def test_adds_frontmatter_when_absent(self):
        content = "# My Plan\n\nSome text here.\n"
        result = apply_frontmatter(content, content_type="task_roadmap")
        assert result.startswith("---\n")
        assert "type: task_roadmap" in result
        assert "# My Plan" in result

    def test_idempotent_when_frontmatter_exists(self):
        content = "---\ntype: task_roadmap\ncreated: 2025-01-01\n---\n\n# My Plan\n"
        result = apply_frontmatter(content, content_type="task_roadmap")
        # Should NOT add a second frontmatter block
        assert result.count("---") == 2  # one opening, one closing
        assert result == content  # unchanged

    def test_adds_created_date(self):
        content = "# Wiki Article\n"
        result = apply_frontmatter(content, content_type="wiki_knowledge")
        assert "created:" in result

    def test_preserves_original_body(self):
        body = "# Original Title\n\nParagraph text that must survive.\n"
        result = apply_frontmatter(body, content_type="brief")
        assert "Paragraph text that must survive." in result


class TestNormalizeHeadings:
    """normalize_headings() should ensure a single H1 exists."""

    def test_no_op_when_h1_present(self):
        content = "---\ntype: x\n---\n\n# Good Title\n\nBody\n"
        assert normalize_headings(content) == content

    def test_promotes_first_h2_when_no_h1(self):
        content = "---\ntype: x\n---\n\n## Section One\n\nBody\n"
        result = normalize_headings(content)
        assert result.startswith("---")
        assert "# Section One\n" in result

    def test_does_not_touch_frontmatter(self):
        content = "---\ntype: x\n---\n\n# Title\n\n## Sub\n"
        result = normalize_headings(content)
        assert result.startswith("---")
        # H1 should still be there and H2 untouched
        assert "# Title\n" in result
        assert "## Sub\n" in result


# ─────────────────────────────────────────────────────────────
# Unit tests — filter_file
# ─────────────────────────────────────────────────────────────


class TestFilterFile:
    def test_filter_file_adds_frontmatter(self, tmp_path):
        root = make_navig_tree(tmp_path)
        md = root / ".navig" / "plans" / "001-my-plan.md"
        md.write_text("# My Plan\n\nTODO items and phases.\n", encoding="utf-8")

        engine = FilteringEngine(root)
        result = engine.filter_file(md, dry_run=False)

        assert result.changed
        assert "type:" in md.read_text(encoding="utf-8")

    def test_filter_file_dry_run_does_not_write(self, tmp_path):
        root = make_navig_tree(tmp_path)
        md = root / ".navig" / "plans" / "002-dry.md"
        original = "# Dry Run Test\n\nContent.\n"
        md.write_text(original, encoding="utf-8")

        engine = FilteringEngine(root)
        result = engine.filter_file(md, dry_run=True)

        assert md.read_text(encoding="utf-8") == original  # unchanged
        assert result.would_change  # engine detected a change is needed

    def test_filter_file_idempotent(self, tmp_path):
        """Running filter twice on an already-clean file must report no change."""
        root = make_navig_tree(tmp_path)
        md = root / ".navig" / "plans" / "003-clean.md"
        clean = "---\ntype: task_roadmap\ncreated: 2025-01-01\n---\n\n# Clean Plan\n"
        md.write_text(clean, encoding="utf-8")

        engine = FilteringEngine(root)
        r1 = engine.filter_file(md, dry_run=False)
        r2 = engine.filter_file(md, dry_run=False)

        assert not r1.changed
        assert not r2.changed

    def test_filter_file_skips_non_markdown(self, tmp_path):
        root = make_navig_tree(tmp_path)
        txt = root / ".navig" / "plans" / "config.yaml"
        txt.write_text("key: value\n", encoding="utf-8")

        engine = FilteringEngine(root)
        result = engine.filter_file(txt, dry_run=False)

        assert result.skipped

    def test_filter_file_logs_error_on_unreadable(self, tmp_path):
        root = make_navig_tree(tmp_path)
        missing = root / ".navig" / "plans" / "ghost.md"  # does not exist

        engine = FilteringEngine(root)
        result = engine.filter_file(missing, dry_run=False)

        assert result.error is not None


# ─────────────────────────────────────────────────────────────
# Integration tests — scan_and_filter
# ─────────────────────────────────────────────────────────────


class TestScanAndFilter:
    def test_empty_directories_return_zero_results(self, tmp_path):
        root = make_navig_tree(tmp_path)
        engine = FilteringEngine(root)
        results = engine.scan_and_filter(dry_run=True)
        assert results == []

    def test_scans_all_navig_subdirectories(self, tmp_path):
        root = make_navig_tree(tmp_path)
        (root / ".navig" / "plans" / "plan.md").write_text("# A\n", encoding="utf-8")
        (root / ".navig" / "wiki" / "guide.md").write_text("# B\n", encoding="utf-8")
        (root / ".navig" / "memory" / "log.md").write_text("# C\n", encoding="utf-8")

        engine = FilteringEngine(root)
        results = engine.scan_and_filter(dry_run=True)
        paths = {r.path.name for r in results}
        assert "plan.md" in paths
        assert "guide.md" in paths
        assert "log.md" in paths

    def test_skips_inbox_directory_files(self, tmp_path):
        """Files inside .navig/plans/inbox/ are router inputs — skip them."""
        root = make_navig_tree(tmp_path)
        inbox = root / ".navig" / "plans" / "inbox"
        inbox.mkdir(exist_ok=True)
        (inbox / "raw-input.md").write_text("# Raw\n", encoding="utf-8")

        engine = FilteringEngine(root)
        results = engine.scan_and_filter(dry_run=True)
        inbox_results = [r for r in results if "inbox" in str(r.path)]
        assert inbox_results == []

    def test_updates_files_in_place(self, tmp_path):
        root = make_navig_tree(tmp_path)
        md = root / ".navig" / "plans" / "needs-update.md"
        md.write_text("# My Plan\n\nPhase 1.\n", encoding="utf-8")

        engine = FilteringEngine(root)
        engine.scan_and_filter(dry_run=False)

        updated = md.read_text(encoding="utf-8")
        assert "---" in updated  # frontmatter added


# ─────────────────────────────────────────────────────────────
# Watch loop test
# ─────────────────────────────────────────────────────────────


class TestWatchLoop:
    def test_watch_fires_on_new_file(self, tmp_path):
        root = make_navig_tree(tmp_path)
        engine = FilteringEngine(root)

        fired: list[Path] = []
        engine.on_change = lambda path: fired.append(path)

        watcher_thread = threading.Thread(
            target=engine.start_watch,
            kwargs={"interval_secs": 0.1, "max_cycles": 5},
            daemon=True,
        )
        watcher_thread.start()

        # Give the watcher one cycle to baseline
        time.sleep(0.15)

        # Drop a new file
        new_file = root / ".navig" / "plans" / "new-plan.md"
        new_file.write_text("# New Plan\n\nItems.\n", encoding="utf-8")

        # Wait for watcher to pick it up
        watcher_thread.join(timeout=2.0)

        assert any(
            p.name == "new-plan.md" for p in fired
        ), "Watch loop did not fire on_change for new file"
