"""Tests for the Inbox Router Agent — heuristic classifier, utilities, and agent."""

import json

import pytest

# ── Heuristic Classifier ───────────────────────────────────


class TestHeuristicClassify:
    def test_roadmap_content(self):
        from navig.agents.inbox_router import heuristic_classify

        ct, conf = heuristic_classify(
            "# Migration Plan\n\nPhase 1: Setup\n- milestone: DB migration\n- sprint 3 tasks"
        )
        assert ct == "task_roadmap"
        assert conf >= 0.5

    def test_brief_content(self):
        from navig.agents.inbox_router import heuristic_classify

        ct, conf = heuristic_classify(
            "# Auth Feature Spec\n\nRequirements:\n- User can login\n- Scope: web only\n"
            "Acceptance criteria: all tests pass"
        )
        assert ct == "brief"
        assert conf >= 0.5

    def test_wiki_content(self):
        from navig.agents.inbox_router import heuristic_classify

        ct, conf = heuristic_classify(
            "# Setup Guide\n\nThis tutorial shows how to install and configure the system.\n"
            "Refer to the architecture documentation for details."
        )
        assert ct == "wiki_knowledge"
        assert conf >= 0.5

    def test_memory_content(self):
        from navig.agents.inbox_router import heuristic_classify

        ct, conf = heuristic_classify(
            "# Session Log\n\nToday's standup: discussed the decision record for auth.\n"
            "Meeting notes: switch to daily retrospective."
        )
        assert ct == "memory_log"
        assert conf >= 0.5

    def test_ambiguous_returns_other(self):
        from navig.agents.inbox_router import heuristic_classify

        ct, conf = heuristic_classify("Hello world. Just a random note.")
        assert ct == "other"
        assert conf < 0.5

    def test_empty_content(self):
        from navig.agents.inbox_router import heuristic_classify

        ct, conf = heuristic_classify("")
        assert ct == "other"
        assert conf < 0.5

    def test_filename_hint_roadmap(self):
        from navig.agents.inbox_router import heuristic_classify

        ct, conf = heuristic_classify("some content", "my-roadmap-draft.md")
        assert ct == "task_roadmap"
        assert conf >= 0.8

    def test_filename_hint_brief(self):
        from navig.agents.inbox_router import heuristic_classify

        ct, conf = heuristic_classify("some content", "auth-spec.md")
        assert ct == "brief"
        assert conf >= 0.8

    def test_filename_hint_wiki(self):
        from navig.agents.inbox_router import heuristic_classify

        ct, conf = heuristic_classify("some content", "setup-guide.md")
        assert ct == "wiki_knowledge"
        assert conf >= 0.8

    def test_filename_hint_memory(self):
        from navig.agents.inbox_router import heuristic_classify

        ct, conf = heuristic_classify("some content", "session-2024-01-15.md")
        assert ct == "memory_log"
        assert conf >= 0.8

    def test_filename_takes_priority(self):
        """Filename hint should override content signals."""
        from navig.agents.inbox_router import heuristic_classify

        # Content says wiki, filename says roadmap
        ct, _ = heuristic_classify(
            "This is a setup guide and tutorial documentation", "sprint-plan.md"
        )
        assert ct == "task_roadmap"


# ── Extract Title ───────────────────────────────────────────


class TestExtractTitle:
    def test_h1_extraction(self):
        from navig.agents.inbox_router import extract_title

        assert extract_title("# My Great Plan\n\nSome content", "file.md") == "My Great Plan"

    def test_h2_not_extracted(self):
        from navig.agents.inbox_router import extract_title

        assert extract_title("## Not This\n\nContent", "fallback.md") == "fallback"

    def test_no_heading_uses_filename(self):
        from navig.agents.inbox_router import extract_title

        assert extract_title("Just some text", "my-doc.md") == "my-doc"

    def test_empty_content(self):
        from navig.agents.inbox_router import extract_title

        assert extract_title("", "notes.md") == "notes"


# ── Numeric Prefix ──────────────────────────────────────────


class TestNextNumericPrefix:
    def test_empty_folder(self, tmp_path):
        from navig.agents.inbox_router import next_numeric_prefix

        assert next_numeric_prefix(tmp_path) == "001"

    def test_nonexistent_folder(self, tmp_path):
        from navig.agents.inbox_router import next_numeric_prefix

        assert next_numeric_prefix(tmp_path / "nope") == "001"

    def test_existing_files(self, tmp_path):
        from navig.agents.inbox_router import next_numeric_prefix

        (tmp_path / "001-first.md").write_text("x")
        (tmp_path / "003-third.md").write_text("x")
        (tmp_path / "not-numbered.md").write_text("x")
        assert next_numeric_prefix(tmp_path) == "004"


# ── Make Target Filename ────────────────────────────────────


class TestMakeTargetFilename:
    def test_basic(self, tmp_path):
        from navig.agents.inbox_router import make_target_filename

        name = make_target_filename("Auth Feature Plan", "brief", tmp_path)
        assert name.startswith("001-")
        assert name.endswith(".md")
        assert "auth-feature-plan" in name

    def test_long_title_truncated(self, tmp_path):
        from navig.agents.inbox_router import make_target_filename

        long_title = "A" * 200
        name = make_target_filename(long_title, "brief", tmp_path)
        # filename slug capped at 60 chars + prefix + .md
        assert len(name) <= 70

    def test_empty_title_uses_type(self, tmp_path):
        from navig.agents.inbox_router import make_target_filename

        name = make_target_filename("", "wiki_knowledge", tmp_path)
        assert "wiki_knowledge" in name


# ── Workspace Metadata ──────────────────────────────────────


class TestCollectWorkspaceMetadata:
    def test_empty_project(self, tmp_path):
        from navig.agents.inbox_router import collect_workspace_metadata

        meta = collect_workspace_metadata(tmp_path)
        assert meta["existing_plans"] == []
        assert meta["existing_briefs"] == []

    def test_with_files(self, tmp_path):
        from navig.agents.inbox_router import collect_workspace_metadata

        plans = tmp_path / ".navig" / "plans"
        plans.mkdir(parents=True)
        (plans / "DEV_PLAN.md").write_text("x")
        briefs = plans / "briefs"
        briefs.mkdir()
        (briefs / "auth-brief.md").write_text("x")
        meta = collect_workspace_metadata(tmp_path)
        assert "DEV_PLAN.md" in meta["existing_plans"]
        assert "auth-brief.md" in meta["existing_briefs"]


# ── List Inbox Files ────────────────────────────────────────


class TestListInboxFiles:
    def test_no_inbox(self, tmp_path):
        from navig.agents.inbox_router import list_inbox_files

        assert list_inbox_files(tmp_path) == []

    def test_finds_md_files(self, tmp_path):
        from navig.agents.inbox_router import list_inbox_files

        inbox = tmp_path / ".navig" / "plans" / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "note1.md").write_text("a")
        (inbox / "note2.md").write_text("b")
        (inbox / "ignore.txt").write_text("c")
        result = list_inbox_files(tmp_path)
        assert len(result) == 2
        assert all(f.suffix == ".md" for f in result)


# ── Agent (Heuristic Mode) ─────────────────────────────────


class TestInboxRouterAgentHeuristic:
    def test_process_single(self, tmp_path):
        from navig.agents.inbox_router import InboxRouterAgent

        inbox = tmp_path / ".navig" / "plans" / "inbox"
        inbox.mkdir(parents=True)
        f = inbox / "roadmap-draft.md"
        f.write_text("# Migration Roadmap\n\nPhase 1: milestone setup\nSprint 1 tasks")
        agent = InboxRouterAgent(tmp_path, use_llm=False)
        plan = agent.process_single(f)
        assert plan["content_type"] == "task_roadmap"
        assert plan["source_file"] == str(f)
        assert "target_path" in plan
        assert "transformed_content" in plan
        assert "---" in plan["transformed_content"]  # frontmatter

    def test_process_single_missing_file(self, tmp_path):
        from navig.agents.inbox_router import InboxRouterAgent

        agent = InboxRouterAgent(tmp_path, use_llm=False)
        plan = agent.process_single(tmp_path / "nope.md")
        assert "error" in plan

    def test_process_batch(self, tmp_path):
        from navig.agents.inbox_router import InboxRouterAgent

        inbox = tmp_path / ".navig" / "plans" / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "plan.md").write_text("# Plan\nSprint 1 milestone")
        (inbox / "guide.md").write_text("# Setup Guide\nHow to install")
        agent = InboxRouterAgent(tmp_path, use_llm=False)
        plans = agent.process_batch()
        assert len(plans) == 2

    def test_process_batch_empty(self, tmp_path):
        from navig.agents.inbox_router import InboxRouterAgent

        agent = InboxRouterAgent(tmp_path, use_llm=False)
        plans = agent.process_batch()
        assert plans == []

    def test_manual_space_wins_over_frontmatter_and_classifier(self, tmp_path):
        from navig.agents.inbox_router import InboxRouterAgent

        inbox = tmp_path / ".navig" / "plans" / "inbox"
        inbox.mkdir(parents=True)
        f = inbox / "roadmap-draft.md"
        f.write_text(
            "---\nspace: career\n---\n\n# Migration Roadmap\n\nPhase 1: milestone setup"
        )

        agent = InboxRouterAgent(tmp_path, use_llm=False)
        plan = agent.process_single(f, manual_space="health")

        assert plan["space"] == "health"
        assert plan["space_source"] == "manual"
        assert "space: health" in plan["transformed_content"]

    def test_frontmatter_space_used_when_manual_missing(self, tmp_path):
        from navig.agents.inbox_router import InboxRouterAgent

        inbox = tmp_path / ".navig" / "plans" / "inbox"
        inbox.mkdir(parents=True)
        f = inbox / "note.md"
        f.write_text("---\nspace: finance\n---\n\n# Notes\n\nrandom text")

        agent = InboxRouterAgent(tmp_path, use_llm=False)
        plan = agent.process_single(f)

        assert plan["space"] == "finance"
        assert plan["space_source"] == "frontmatter"
        assert "space: finance" in plan["transformed_content"]

    def test_default_space_is_default_when_unattributed(self, tmp_path):
        from navig.agents.inbox_router import InboxRouterAgent

        inbox = tmp_path / ".navig" / "plans" / "inbox"
        inbox.mkdir(parents=True)
        f = inbox / "note.md"
        f.write_text("just random text")

        agent = InboxRouterAgent(tmp_path, use_llm=False)
        plan = agent.process_single(f)

        assert plan["space"] == "default"
        assert plan["space_source"] in {"default", "classifier"}
        assert "space: default" in plan["transformed_content"]


# ── Execute Plan ────────────────────────────────────────────


class TestExecutePlan:
    def test_dry_run(self, tmp_path):
        from navig.agents.inbox_router import execute_plan

        plan = {
            "content_type": "brief",
            "target_path": ".navig/plans/briefs/001-test.md",
            "transformed_content": "# Test Brief\ncontent",
            "source_file": str(tmp_path / "inbox" / "raw.md"),
        }
        result = execute_plan(tmp_path, plan, dry_run=True)
        assert result["status"] == "dry_run"
        assert not (tmp_path / ".navig" / "plans" / "briefs" / "001-test.md").exists()

    def test_write(self, tmp_path):
        from navig.agents.inbox_router import execute_plan

        # Create source file so move can work
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        src = inbox / "raw.md"
        src.write_text("original")

        plan = {
            "content_type": "wiki_knowledge",
            "target_path": ".navig/wiki/001-test-doc.md",
            "transformed_content": "---\ntype: wiki_knowledge\n---\n\n# Test",
            "source_file": str(src),
        }
        result = execute_plan(tmp_path, plan, dry_run=False, move_source=True)
        assert result["status"] == "written"
        target = tmp_path / ".navig" / "wiki" / "001-test-doc.md"
        assert target.exists()
        assert "wiki_knowledge" in target.read_text()
        assert not src.exists()  # moved
        assert (inbox / ".processed" / "raw.md").exists()

    def test_no_target_keeps_in_inbox(self, tmp_path):
        from navig.agents.inbox_router import execute_plan

        plan = {
            "content_type": "other",
            "target_path": None,
            "rationale": "ambiguous",
            "source_file": "test.md",
        }
        result = execute_plan(tmp_path, plan)
        assert result["status"] == "kept_in_inbox"

    def test_error_plan(self, tmp_path):
        from navig.agents.inbox_router import execute_plan

        plan = {"error": "File not found", "source_file": "bad.md"}
        result = execute_plan(tmp_path, plan)
        assert result["status"] == "error"


# ── Constants ───────────────────────────────────────────────


class TestConstants:
    def test_content_types(self):
        from navig.agents.inbox_router import CONTENT_TYPES

        assert len(CONTENT_TYPES) == 5
        assert "task_roadmap" in CONTENT_TYPES
        assert "brief" in CONTENT_TYPES
        assert "wiki_knowledge" in CONTENT_TYPES
        assert "memory_log" in CONTENT_TYPES
        assert "other" in CONTENT_TYPES

    def test_target_folders(self):
        from navig.agents.inbox_router import CONTENT_TYPES, TARGET_FOLDERS

        for ct in CONTENT_TYPES:
            assert ct in TARGET_FOLDERS
        assert TARGET_FOLDERS["other"] is None

    def test_system_prompt_not_empty(self):
        from navig.agents.inbox_router import INBOX_ROUTER_SYSTEM_PROMPT

        assert len(INBOX_ROUTER_SYSTEM_PROMPT) > 100
        assert "Inbox Router" in INBOX_ROUTER_SYSTEM_PROMPT


# ── Parse LLM Response ─────────────────────────────────────


class TestParseLLMResponse:
    def _agent(self, tmp_path):
        from navig.agents.inbox_router import InboxRouterAgent

        return InboxRouterAgent(tmp_path, use_llm=False)

    def test_plain_json(self, tmp_path):
        agent = self._agent(tmp_path)
        raw = '{"content_type": "brief", "confidence": 0.9}'
        result = agent._parse_llm_response(raw)
        assert result["content_type"] == "brief"

    def test_fenced_json(self, tmp_path):
        agent = self._agent(tmp_path)
        raw = '```json\n{"content_type": "wiki_knowledge"}\n```'
        result = agent._parse_llm_response(raw)
        assert result["content_type"] == "wiki_knowledge"

    def test_fenced_no_lang(self, tmp_path):
        agent = self._agent(tmp_path)
        raw = '```\n{"content_type": "memory_log"}\n```'
        result = agent._parse_llm_response(raw)
        assert result["content_type"] == "memory_log"

    def test_invalid_json_raises(self, tmp_path):
        agent = self._agent(tmp_path)
        with pytest.raises(json.JSONDecodeError):
            agent._parse_llm_response("not json at all")
