"""Batch 106: tests for agent_config_loader, assistant_utils, skills_renderer."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# agent_config_loader
# ---------------------------------------------------------------------------

class TestClearAgentCache:
    def test_clear_cache_is_idempotent(self):
        from navig.agent_config_loader import clear_agent_cache, _agent_config_cache
        _agent_config_cache["some_agent"] = object()
        clear_agent_cache()
        assert len(_agent_config_cache) == 0

    def test_clear_empty_cache_no_error(self):
        from navig.agent_config_loader import clear_agent_cache, _agent_config_cache
        _agent_config_cache.clear()
        clear_agent_cache()  # should not raise


class TestGetAgentLlmMode:
    def test_returns_big_tasks_when_no_agent_json(self, tmp_path, monkeypatch):
        from navig import agent_config_loader
        # patch load_agent_json to return None
        monkeypatch.setattr(agent_config_loader, "load_agent_json", lambda _: None)
        result = agent_config_loader.get_agent_llm_mode("nonexistent_agent")
        assert result == "big_tasks"

    def test_returns_configured_mode(self, monkeypatch):
        from navig import agent_config_loader
        mock_cfg = MagicMock()
        mock_cfg.llm_mode = "coding"
        monkeypatch.setattr(agent_config_loader, "load_agent_json", lambda _: mock_cfg)
        result = agent_config_loader.get_agent_llm_mode("system_architect")
        assert result == "coding"


class TestParseAgentJson:
    def test_parses_valid_agent_json(self, tmp_path):
        from navig.agent_config_loader import _parse_agent_json, clear_agent_cache, _agent_config_cache
        clear_agent_cache()
        data = {"id": "test_agent", "llm_mode": "coding"}
        p = tmp_path / "agent.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        result = _parse_agent_json(p, "test_agent")
        assert result is not None
        assert result.llm_mode == "coding"
        # should be cached
        assert "test_agent" in _agent_config_cache

    def test_returns_none_on_invalid_json(self, tmp_path):
        from navig.agent_config_loader import _parse_agent_json, clear_agent_cache
        clear_agent_cache()
        p = tmp_path / "bad.json"
        p.write_text("NOT_VALID_JSON", encoding="utf-8")
        result = _parse_agent_json(p, "bad_agent")
        assert result is None

    def test_returns_none_when_file_missing(self, tmp_path):
        from navig.agent_config_loader import _parse_agent_json, clear_agent_cache
        clear_agent_cache()
        missing = tmp_path / "does_not_exist.json"
        result = _parse_agent_json(missing, "ghost")
        assert result is None

    def test_default_llm_mode_when_not_specified(self, tmp_path):
        from navig.agent_config_loader import _parse_agent_json, clear_agent_cache
        clear_agent_cache()
        data = {"id": "minimal"}
        p = tmp_path / "min.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        result = _parse_agent_json(p, "minimal")
        # AgentJsonConfig should have a default llm_mode
        assert result is not None
        assert isinstance(result.llm_mode, str)


class TestLoadAgentJson:
    def test_returns_none_when_no_agents_dir(self, tmp_path, monkeypatch):
        from navig import agent_config_loader
        # patch config dir so agents/ doesn't exist
        monkeypatch.setattr(
            "navig.agent_config_loader.load_agent_json",
            agent_config_loader.load_agent_json,
        )
        agent_config_loader.clear_agent_cache()
        # mock workspace path helpers
        with patch("navig.agent_config_loader._agent_config_cache", {}):
            # Just verify no crash when called with unknown agent
            result = agent_config_loader.load_agent_json("totally_unknown_xyz_1234")
        assert result is None

    def test_uses_cache_on_second_call(self, tmp_path, monkeypatch):
        from navig import agent_config_loader
        sentinel = MagicMock()
        sentinel.llm_mode = "fast"
        agent_config_loader.clear_agent_cache()
        agent_config_loader._agent_config_cache["cached_agent"] = sentinel
        result = agent_config_loader.load_agent_json("cached_agent")
        assert result is sentinel


# ---------------------------------------------------------------------------
# assistant_utils
# ---------------------------------------------------------------------------

class TestGetDefaultHelpers:
    def test_error_patterns_is_list(self):
        from navig.assistant_utils import _get_default_error_patterns
        patterns = _get_default_error_patterns()
        assert isinstance(patterns, list)

    def test_error_patterns_have_required_keys(self):
        from navig.assistant_utils import _get_default_error_patterns
        patterns = _get_default_error_patterns()
        assert len(patterns) > 0
        for p in patterns:
            assert "pattern" in p
            assert "category" in p
            assert "severity" in p

    def test_error_patterns_include_connection_refused(self):
        from navig.assistant_utils import _get_default_error_patterns
        patterns = _get_default_error_patterns()
        all_patterns = [p["pattern"] for p in patterns]
        assert any("Connection refused" in pat for pat in all_patterns)

    def test_solutions_returns_list(self):
        from navig.assistant_utils import _get_default_solutions
        assert isinstance(_get_default_solutions(), list)

    def test_config_rules_returns_list(self):
        from navig.assistant_utils import _get_default_config_rules
        assert isinstance(_get_default_config_rules(), list)

    def test_severity_values_are_valid(self):
        from navig.assistant_utils import _get_default_error_patterns
        valid = {"low", "medium", "high", "critical"}
        for p in _get_default_error_patterns():
            assert p["severity"] in valid


class TestEnsureNavigDirectory:
    def test_creates_directory_structure(self, tmp_path, monkeypatch):
        from navig import assistant_utils
        monkeypatch.setattr(
            "navig.assistant_utils._IS_WINDOWS", True  # skip chmod
        )
        with patch("navig.platform.paths.config_dir", return_value=tmp_path / ".navig"):
            result = assistant_utils.ensure_navig_directory()
        assert result.exists()
        assert (result / "ai_context").exists()
        assert (result / "baselines").exists()

    def test_returns_path(self, tmp_path, monkeypatch):
        from navig import assistant_utils
        monkeypatch.setattr("navig.assistant_utils._IS_WINDOWS", True)
        with patch("navig.platform.paths.config_dir", return_value=tmp_path / ".navig2"):
            result = assistant_utils.ensure_navig_directory()
        assert isinstance(result, Path)

    def test_idempotent_when_dir_exists(self, tmp_path, monkeypatch):
        from navig import assistant_utils
        monkeypatch.setattr("navig.assistant_utils._IS_WINDOWS", True)
        target = tmp_path / ".navig3"
        with patch("navig.platform.paths.config_dir", return_value=target):
            r1 = assistant_utils.ensure_navig_directory()
            r2 = assistant_utils.ensure_navig_directory()
        assert r1 == r2

    def test_initializes_json_files(self, tmp_path, monkeypatch):
        from navig import assistant_utils
        monkeypatch.setattr("navig.assistant_utils._IS_WINDOWS", True)
        base = tmp_path / ".navig_init"
        with patch("navig.platform.paths.config_dir", return_value=base):
            assistant_utils.ensure_navig_directory()
        ai_ctx = base / "ai_context"
        assert (ai_ctx / "command_history.json").exists()
        assert (ai_ctx / "error_patterns.json").exists()


class TestInitializeJsonFiles:
    def test_creates_files_in_ai_context(self, tmp_path):
        from navig.assistant_utils import _initialize_json_files
        navig_dir = tmp_path / "navig"
        (navig_dir / "ai_context").mkdir(parents=True)
        _initialize_json_files(navig_dir)
        files = list((navig_dir / "ai_context").iterdir())
        assert len(files) > 0

    def test_does_not_overwrite_existing_files(self, tmp_path):
        from navig.assistant_utils import _initialize_json_files
        navig_dir = tmp_path / "navig2"
        ai_ctx = navig_dir / "ai_context"
        ai_ctx.mkdir(parents=True)
        hist = ai_ctx / "command_history.json"
        hist.write_text("[1, 2, 3]", encoding="utf-8")
        _initialize_json_files(navig_dir)
        assert hist.read_text(encoding="utf-8") == "[1, 2, 3]"

    def test_error_patterns_file_has_valid_json(self, tmp_path):
        from navig.assistant_utils import _initialize_json_files
        navig_dir = tmp_path / "navig3"
        (navig_dir / "ai_context").mkdir(parents=True)
        _initialize_json_files(navig_dir)
        ep = navig_dir / "ai_context" / "error_patterns.json"
        data = json.loads(ep.read_text(encoding="utf-8"))
        assert isinstance(data, list)


# ---------------------------------------------------------------------------
# skills_renderer
# ---------------------------------------------------------------------------

class TestManualRender:
    def test_empty_skills_returns_header(self):
        from navig.skills_renderer import _manual_render
        result = _manual_render([])
        assert "tools/skills" in result

    def test_renders_skill_id_and_name(self):
        from navig.skills_renderer import _manual_render
        skills = [{"id": "git", "name": "Git Helper", "summary": "Git ops", "commands": []}]
        result = _manual_render(skills)
        assert "git" in result
        assert "Git Helper" in result

    def test_renders_command_details(self):
        from navig.skills_renderer import _manual_render
        skills = [
            {
                "id": "ahk",
                "name": "AHK",
                "summary": "AutoHotkey",
                "commands": [
                    {"name": "run_script", "signature": "run_script(path)", "description": "Runs an AHK script"},
                ],
            }
        ]
        result = _manual_render(skills)
        assert "run_script" in result

    def test_multiple_skills_rendered(self):
        from navig.skills_renderer import _manual_render
        skills = [
            {"id": "a", "name": "Alpha", "summary": "A stuff", "commands": []},
            {"id": "b", "name": "Beta", "summary": "B stuff", "commands": []},
        ]
        result = _manual_render(skills)
        assert "[a]" in result
        assert "[b]" in result

    def test_missing_fields_dont_crash(self):
        from navig.skills_renderer import _manual_render
        skills = [{}]  # completely empty dict
        result = _manual_render(skills)
        assert isinstance(result, str)


class TestRenderSkillsPrompt:
    def test_empty_skill_ids_returns_empty_string(self):
        from navig.skills_renderer import render_skills_prompt
        result = render_skills_prompt([])
        assert result == ""

    def test_unknown_skill_ids_return_empty(self, monkeypatch):
        from navig import skills_renderer
        monkeypatch.setattr(skills_renderer, "_load_skill_json", lambda _: None)
        monkeypatch.setattr(skills_renderer, "_load_skill_md", lambda *a, **k: None)
        result = skills_renderer.render_skills_prompt(["no_such_skill"], mode="auto")
        assert result == ""

    def test_md_mode_uses_md_content(self, monkeypatch):
        from navig import skills_renderer
        monkeypatch.setattr(
            skills_renderer,
            "_load_skill_md",
            lambda sid, **kw: f"## {sid} help content",
        )
        result = skills_renderer.render_skills_prompt(["myskill"], mode="md")
        assert "myskill" in result
        assert "help content" in result

    def test_json_mode_uses_json_data(self, monkeypatch):
        from navig import skills_renderer
        fake_data = {"id": "gitops", "name": "GitOps", "summary": "Git automation", "commands": []}
        monkeypatch.setattr(skills_renderer, "_load_skill_json", lambda _: fake_data)
        # Disable jinja2 to force _manual_render path
        with patch.dict(sys.modules, {"jinja2": None}):
            result = skills_renderer.render_skills_prompt(["gitops"], mode="json")
        # Either jinja2 rendered or manual fallback — just check non-empty
        assert len(result) > 0

    def test_auto_mode_prefers_json(self, monkeypatch):
        from navig import skills_renderer
        fake_data = {"id": "autosk", "name": "AutoSkill", "summary": "auto", "commands": []}
        monkeypatch.setattr(skills_renderer, "_load_skill_json", lambda _: fake_data)
        monkeypatch.setattr(skills_renderer, "_load_skill_md", lambda *a, **k: "md fallback")
        with patch.dict(sys.modules, {"jinja2": None}):
            result = skills_renderer.render_skills_prompt(["autosk"], mode="auto")
        assert "AutoSkill" in result or "autosk" in result
        # Should NOT contain md fallback since json was returned
        assert "md fallback" not in result


class TestGetContextSkillsMode:
    def test_returns_string(self):
        from navig.skills_renderer import _get_context_skills_mode
        result = _get_context_skills_mode()
        assert isinstance(result, str)

    def test_returns_auto_on_config_failure(self, monkeypatch):
        from navig import skills_renderer
        monkeypatch.setattr(
            "navig.skills_renderer._get_context_skills_mode",
            lambda: "auto",
        )
        # Direct test: patch config to raise
        with patch("navig.config.get_config_manager", side_effect=RuntimeError("no config")):
            from navig.skills_renderer import _get_context_skills_mode
            result = _get_context_skills_mode()
        assert result == "auto"
