"""
Tests for navig.inbox — store, classifier, router, hooks.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


# ── Store ────────────────────────────────────────────────────

class TestInboxStore:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        from navig.inbox.store import InboxStore
        self.store = InboxStore(db_path=Path(self.tmp) / "inbox_test.db")

    def test_insert_and_get_event(self):
        from navig.inbox.store import InboxEvent
        event = InboxEvent(source_path="/tmp/test.md", filename="test.md", source_type="file")
        eid = self.store.insert_event(event)
        assert eid > 0
        fetched = self.store.get_event(eid)
        assert fetched is not None
        assert fetched.filename == "test.md"
        assert fetched.status == "pending"

    def test_update_event_status(self):
        from navig.inbox.store import InboxEvent
        event = InboxEvent(source_path="/tmp/x.md", filename="x.md")
        eid = self.store.insert_event(event)
        self.store.update_event_status(eid, "routed")
        fetched = self.store.get_event(eid)
        assert fetched.status == "routed"

    def test_insert_decision(self):
        from navig.inbox.store import InboxEvent, RoutingDecision
        event = InboxEvent(source_path="/tmp/a.md", filename="a.md")
        eid = self.store.insert_event(event)

        decision = RoutingDecision(
            event_id=eid,
            category="wiki/knowledge",
            confidence=0.85,
            mode="copy",
            destination="/target/a.md",
            executed=True,
            result_path="/target/a.md",
        )
        did = self.store.insert_decision(decision)
        assert did > 0

        decisions = self.store.decisions_for_event(eid)
        assert len(decisions) == 1
        assert decisions[0].category == "wiki/knowledge"

    def test_list_events_by_status(self):
        from navig.inbox.store import InboxEvent
        for i in range(3):
            e = InboxEvent(source_path=f"/tmp/f{i}.md", filename=f"f{i}.md", status="pending")
            self.store.insert_event(e)
        e = InboxEvent(source_path="/tmp/routed.md", filename="routed.md", status="routed")
        self.store.insert_event(e)

        pending = self.store.list_events(status="pending")
        assert len(pending) == 3

    def test_stats(self):
        from navig.inbox.store import InboxEvent, RoutingDecision
        e = InboxEvent(source_path="/tmp/b.md", filename="b.md", status="routed")
        eid = self.store.insert_event(e)
        d = RoutingDecision(event_id=eid, category="wiki/technical", mode="copy", destination="/x")
        self.store.insert_decision(d)

        stats = self.store.stats()
        assert stats["total_events"] >= 1
        assert "wiki/technical" in stats.get("by_category", {})

    def test_mark_decision_executed(self):
        from navig.inbox.store import InboxEvent, RoutingDecision
        e = InboxEvent(source_path="/tmp/c.md", filename="c.md")
        eid = self.store.insert_event(e)
        d = RoutingDecision(event_id=eid, category="hub/tasks", mode="copy", destination="/x")
        did = self.store.insert_decision(d)
        self.store.mark_decision_executed(did, "/target/c.md")
        decisions = self.store.decisions_for_event(eid)
        assert decisions[0].executed is True
        assert decisions[0].result_path == "/target/c.md"


# ── Classifier ───────────────────────────────────────────────

class TestClassifier:
    def setup_method(self):
        from navig.inbox.classifier import Classifier
        self.clf = Classifier(use_llm=False)

    def test_classify_returns_result(self):
        result = self.clf.classify("This is a guide for using Docker containers")
        assert result.category != ""
        assert 0.0 <= result.confidence <= 1.0
        assert result.method == "bm25"

    def test_classify_technical_content(self):
        result = self.clf.classify(
            "API endpoint design and database schema migration for the new service",
            filename="api_design.md",
        )
        assert result.category == "wiki/technical"

    def test_classify_task_content(self):
        result = self.clf.classify(
            "TODO: implement authentication. Sprint backlog item priority high.",
            filename="todo.md",
        )
        assert result.category == "hub/tasks"

    def test_classify_roadmap_content(self):
        result = self.clf.classify(
            "Q2 2026 roadmap milestone v2.0 release strategy phase 3",
            filename="roadmap.md",
        )
        assert result.category == "hub/roadmap"

    def test_classify_knowledge_content(self):
        result = self.clf.classify(
            "Introduction to concepts and definitions for new team members",
            filename="guide.md",
        )
        assert result.category == "wiki/knowledge"

    def test_classify_business_content(self):
        result = self.clf.classify(
            "Investor pitch deck ROI analysis market acquisition strategy",
            filename="pitch.md",
        )
        assert result.category == "external/business"

    def test_classify_empty_content(self):
        # Empty content should return a valid result (archive or low confidence)
        result = self.clf.classify("", filename="empty.md")
        assert result.category in ("archive", "ignore")
        assert result.confidence <= 0.5

    def test_classify_filename_signal(self):
        result = self.clf.classify("", filename="CHANGELOG.md")
        assert result.category == "hub/changelog"

    def test_alternatives_populated(self):
        result = self.clf.classify(
            "Technical guide tutorial for the API endpoint documentation"
        )
        # Should have alternatives when there's a tie or close second
        assert isinstance(result.alternatives, list)


# ── Router ────────────────────────────────────────────────────

class TestInboxRouter:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        from navig.inbox.router import InboxRouter, RouteMode, ConflictStrategy
        self.router = InboxRouter(
            project_root=self.tmp,
            mode=RouteMode.COPY,
            conflict=ConflictStrategy.RENAME,
            dest_override={"wiki/knowledge": str(self.tmp / "out" / "knowledge")},
        )

    def _make_file(self, name: str, content: str = "test content") -> Path:
        f = self.tmp / name
        f.write_text(content, encoding="utf-8")
        return f

    def _classify(self, category: str, confidence: float = 0.8):
        from navig.inbox.classifier import ClassifyResult
        return ClassifyResult(category=category, confidence=confidence)

    def test_route_copies_file(self):
        src = self._make_file("note.md")
        result = self.router.route(src, self._classify("wiki/knowledge"))
        assert result.status == "routed"
        assert result.result_path is not None
        assert Path(result.result_path).is_file()
        assert src.is_file()  # source still exists (copy mode)

    def test_route_dry_run_no_file_written(self):
        src = self._make_file("dry.md")
        result = self.router.route(src, self._classify("wiki/knowledge"), dry_run=True)
        assert result.status == "routed"
        assert result.result_path is None
        # Destination dir was NOT created
        dest = self.tmp / "out" / "knowledge"
        assert not (dest / "dry.md").exists()

    def test_route_low_confidence_ignored(self):
        src = self._make_file("low.md")
        result = self.router.route(src, self._classify("wiki/knowledge", confidence=0.1))
        assert result.status == "ignored"

    def test_route_ignore_category(self):
        src = self._make_file("ignore_me.md")
        result = self.router.route(src, self._classify("ignore"))
        assert result.status == "ignored"

    def test_conflict_rename(self):
        from navig.inbox.router import ConflictStrategy
        dest_dir = self.tmp / "out" / "knowledge"
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Pre-create the would-be destination
        (dest_dir / "note.md").write_text("existing", encoding="utf-8")

        src = self._make_file("note.md", "new content")
        self.router.conflict = ConflictStrategy.RENAME
        result = self.router.route(src, self._classify("wiki/knowledge"))
        assert result.status == "routed"
        # Should be note_1.md
        assert "note_1.md" in result.result_path

    def test_conflict_skip(self):
        from navig.inbox.router import ConflictStrategy
        dest_dir = self.tmp / "out" / "knowledge"
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "note.md").write_text("existing", encoding="utf-8")

        src = self._make_file("note.md")
        self.router.conflict = ConflictStrategy.SKIP
        result = self.router.route(src, self._classify("wiki/knowledge"))
        assert result.status == "skipped"

    def test_route_url(self):
        result = self.router.route_url(
            url="https://example.com/article",
            content="API endpoint guide and tutorial",
            filename="article.md",
            classify_result=self._classify("wiki/knowledge"),
        )
        assert result.status == "routed"
        dest = Path(result.result_path)
        assert dest.is_file()
        assert dest.read_text() == "API endpoint guide and tutorial"


# ── Hooks ────────────────────────────────────────────────────

class TestHookSystem:
    def setup_method(self):
        from navig.inbox.hooks import HookSystem
        self.hooks = HookSystem()

    def _make_event(self, stage="before_classify"):
        from navig.inbox.hooks import HookEvent
        return HookEvent(
            stage=stage,
            source_path="/tmp/test.md",
            source_type="file",
            filename="test.md",
            content="hello world",
        )

    def test_register_and_fire(self):
        fired = []

        @self.hooks.register("before_classify")
        def h(event):
            fired.append(event.filename)
            return event

        event = self._make_event()
        self.hooks.fire("before_classify", event)
        assert fired == ["test.md"]

    def test_hook_can_modify_event(self):
        @self.hooks.register("after_classify")
        def h(event):
            event.category = "wiki/knowledge"
            return event

        event = self._make_event("after_classify")
        result = self.hooks.fire("after_classify", event)
        assert result.category == "wiki/knowledge"

    def test_hook_returning_none_keeps_event(self):
        @self.hooks.register("before_classify")
        def h(event):
            return None  # no modification

        event = self._make_event()
        result = self.hooks.fire("before_classify", event)
        assert result.filename == "test.md"

    def test_hook_abort_propagates(self):
        from navig.inbox.hooks import HookAbort

        @self.hooks.register("before_classify")
        def h(event):
            raise HookAbort("private file")

        with pytest.raises(HookAbort, match="private file"):
            self.hooks.fire("before_classify", self._make_event())

    def test_unknown_stage_raises(self):
        with pytest.raises(ValueError, match="Unknown hook stage"):
            self.hooks.register("nonexistent_stage", lambda e: e)

    def test_clear_hooks(self):
        called = []

        @self.hooks.register("before_classify")
        def h(event):
            called.append(1)
            return event

        self.hooks.clear("before_classify")
        self.hooks.fire("before_classify", self._make_event())
        assert called == []

    def test_decorator_usage(self):
        """Test register used as a decorator factory (fn=None path)."""
        results = []

        decorator = self.hooks.register("after_route")
        assert callable(decorator)

        decorated = decorator(lambda e: results.append("hit") or e)
        event = self._make_event("after_route")
        self.hooks.fire("after_route", event)
        assert "hit" in results
