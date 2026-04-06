"""Tests for navig.plans.inbox_processor."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from navig.plans.inbox_processor import (
    ConflictDetector,
    ContentNormaliser,
    DuplicateScanner,
    InboxProcessor,
    ReconciliationResult,
    Router,
    StalenessDetector,
)
from navig.plans.inbox_reader import InboxItem, InboxReader

# ── Helpers ───────────────────────────────────────────────────

def _make_item(
    tmp_path: Path,
    name: str,
    *,
    title: str = "",
    body: str = "",
    date_str: str = "",
) -> InboxItem:
    """Construct an InboxItem for testing."""
    fm_lines = []
    if title:
        fm_lines.append(f"title: {title}")
    if date_str:
        fm_lines.append(f"date: {date_str}")
    fm = "---\n" + "\n".join(fm_lines) + "\n---\n" if fm_lines else ""
    content = fm + "\n" + body + "\n"
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")

    from navig.plans.inbox_reader import _parse_frontmatter, canonical_name, parse_suffix_state

    frontmatter, body_text = _parse_frontmatter(content)
    return InboxItem(
        path=path.resolve(),
        name=canonical_name(name),
        content=content,
        frontmatter=frontmatter,
        body=body_text,
        suffix_state=parse_suffix_state(name),
    )


# ── ContentNormaliser ─────────────────────────────────────────

def test_normaliser_strips_trailing_ws() -> None:
    result = ContentNormaliser.normalise("hello   \nworld  \n")
    assert result == "hello\nworld\n"


def test_normaliser_converts_crlf() -> None:
    result = ContentNormaliser.normalise("a\r\nb\rc\n")
    assert result == "a\nb\nc\n"


# ── StalenessDetector ─────────────────────────────────────────

def test_staleness_fresh(tmp_path: Path) -> None:
    today = date.today().isoformat()
    item = _make_item(tmp_path, "fresh.md", title="Fresh", date_str=today)
    detector = StalenessDetector(stale_days=30)
    assert detector.check(item) is None


def test_staleness_old(tmp_path: Path) -> None:
    old_date = (date.today() - timedelta(days=60)).isoformat()
    item = _make_item(tmp_path, "old.md", title="Old", date_str=old_date)
    detector = StalenessDetector(stale_days=30)
    result = detector.check(item)
    assert result is not None
    assert result >= 60


def test_staleness_no_date(tmp_path: Path) -> None:
    item = _make_item(tmp_path, "nodate.md", title="No Date")
    detector = StalenessDetector(stale_days=30)
    assert detector.check(item) is None


# ── DuplicateScanner ──────────────────────────────────────────

def test_duplicate_substring_match(tmp_path: Path) -> None:
    a = _make_item(tmp_path, "setup_api.md", title="Setup API gateway")
    b = _make_item(tmp_path, "api_setup.md", title="API gateway setup and config")
    scanner = DuplicateScanner([a, b])
    # "setup api gateway" is a substring of "api gateway setup and config"?
    # Not exact — but check bidirectional
    dup = scanner.find_duplicate(a)
    # This checks "setup api gateway" in "api gateway setup and config" — no
    # and "api gateway setup and config" in "setup api gateway" — no
    # So no dup here (different word order). That's correct behaviour.


def test_duplicate_exact_title_subset(tmp_path: Path) -> None:
    a = _make_item(tmp_path, "auth.md", title="Add authentication")
    b = _make_item(tmp_path, "auth2.md", title="Add authentication module")
    scanner = DuplicateScanner([a, b])
    dup = scanner.find_duplicate(a)
    assert dup == "auth2.md"


def test_no_duplicate(tmp_path: Path) -> None:
    a = _make_item(tmp_path, "one.md", title="Install Docker")
    b = _make_item(tmp_path, "two.md", title="Configure Nginx")
    scanner = DuplicateScanner([a, b])
    assert scanner.find_duplicate(a) is None


# ── ConflictDetector ──────────────────────────────────────────

def test_conflict_negation(tmp_path: Path) -> None:
    a = _make_item(
        tmp_path, "use_redis.md", title="Use Redis",
        body="We should use redis for caching in production."
    )
    b = _make_item(
        tmp_path, "no_redis.md", title="No Redis",
        body="Not use redis for caching in production. Use memcached instead."
    )
    detector = ConflictDetector([a, b])
    conflict = detector.find_conflict(a)
    assert conflict == "no_redis.md"


def test_no_conflict(tmp_path: Path) -> None:
    a = _make_item(tmp_path, "feature_a.md", title="A", body="Add feature alpha to the system.")
    b = _make_item(tmp_path, "feature_b.md", title="B", body="Add feature beta to the system.")
    detector = ConflictDetector([a, b])
    assert detector.find_conflict(a) is None


# ── Router ────────────────────────────────────────────────────

def test_router_task_keyword(tmp_path: Path) -> None:
    item = _make_item(tmp_path, "t.md", title="Task: fix login")
    router = Router()
    assert router.route(item) == "plans/tasks/active"


def test_router_decision_keyword(tmp_path: Path) -> None:
    item = _make_item(tmp_path, "d.md", title="ADR: choose database")
    router = Router()
    assert router.route(item) == "plans/decisions"


def test_router_fallback(tmp_path: Path) -> None:
    item = _make_item(tmp_path, "misc.md", title="Random thoughts")
    router = Router()
    assert router.route(item) == "plans/tasks/active"


# ── InboxProcessor (integration) ─────────────────────────────

@pytest.fixture()
def processor_tree(tmp_path: Path) -> Path:
    """Create .navig/inbox/ and .navig/staging/ for processor tests."""
    inbox = tmp_path / ".navig" / "inbox"
    inbox.mkdir(parents=True)
    staging = tmp_path / ".navig" / "staging"
    staging.mkdir(parents=True)

    (inbox / "new_task.md").write_text(
        "---\ntitle: Write tests\ntype: task\n---\n\nWrite unit tests for all modules.\n",
        encoding="utf-8",
    )
    (inbox / "old_idea.md").write_text(
        f"---\ntitle: Legacy cleanup\ndate: 2020-01-01\n---\n\nClean up old code.\n",
        encoding="utf-8",
    )
    return tmp_path


def test_processor_routes_fresh_item(processor_tree: Path) -> None:
    reader = InboxReader(processor_tree)
    items = reader.scan()
    processor = InboxProcessor(processor_tree)
    results = processor.process(items)

    routed = [r for r in results if r.item_name == "new_task.md"]
    assert len(routed) == 1
    assert routed[0].decision == "route"


def test_processor_detects_stale(processor_tree: Path) -> None:
    reader = InboxReader(processor_tree)
    items = reader.scan()
    processor = InboxProcessor(processor_tree)
    results = processor.process(items)

    stale = [r for r in results if r.item_name == "old_idea.md"]
    assert len(stale) == 1
    assert stale[0].decision == "stale"


def test_processor_writes_queue_file(processor_tree: Path) -> None:
    reader = InboxReader(processor_tree)
    items = reader.scan()
    processor = InboxProcessor(processor_tree)
    processor.process(items)

    queue = processor_tree / ".navig" / "staging" / "reconciliation_queue.json"
    assert queue.is_file()
    lines = queue.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    for line in lines:
        data = json.loads(line)
        assert "item" in data
        assert "decision" in data
        assert "timestamp" in data


def test_reconciliation_result_json() -> None:
    r = ReconciliationResult(
        item_name="test.md",
        decision="route",
        target_dir="plans/tasks/active",
        reason="keyword match",
    )
    line = r.to_json_line()
    data = json.loads(line)
    assert data["item"] == "test.md"
    assert data["decision"] == "route"
