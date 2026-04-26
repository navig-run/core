"""Tests for navig.tools.skill_runner — SkillRunTool."""
from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.tools.skill_runner import SkillRunTool, _find_navig_bin


# ── helpers ──────────────────────────────────────────────────


def _tool() -> SkillRunTool:
    return SkillRunTool()


def _make_proc_result(
    stdout: str = "output",
    stderr: str = "",
    returncode: int = 0,
    termination: str = "exit",
    elapsed_ms: float = 100.0,
    truncated: bool = False,
):
    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    r.termination = termination
    r.elapsed_ms = elapsed_ms
    r.truncated = truncated
    return r


def _make_skill(skill_id: str = "my-skill"):
    skill = MagicMock()
    skill.name = skill_id
    skill.version = "1.0"
    skill.safety = "safe"
    skill.category = "dev"
    skill.tags = ["tools"]
    skill.body_markdown = "# My Skill\nDoes things."
    return skill


# ── metadata ──────────────────────────────────────────────────


class TestMetadata:
    def test_name(self):
        assert _tool().name == "skill_run"

    def test_description_set(self):
        assert _tool().description

    def test_owner_only_not_set(self):
        # owner_only defaults to False on BaseTool
        assert _tool().owner_only is False


# ── validation ────────────────────────────────────────────────


class TestValidation:
    @pytest.mark.asyncio
    async def test_missing_skill_id_fails(self):
        result = await _tool().run({})
        assert result.success is False
        assert "skill_id" in result.error.lower()

    @pytest.mark.asyncio
    async def test_empty_skill_id_fails(self):
        result = await _tool().run({"skill_id": "   "})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_skill_id_not_in_index_fails(self):
        index = {"other-skill": _make_skill("other-skill")}
        with patch("navig.skills.loader.skills_by_id", return_value=index):
            result = await _tool().run({"skill_id": "missing", "command": "run"})
        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_error_lists_available_skills(self):
        index = {"skill-a": _make_skill("skill-a"), "skill-b": _make_skill("skill-b")}
        with patch("navig.skills.loader.skills_by_id", return_value=index):
            result = await _tool().run({"skill_id": "missing", "command": "run"})
        assert "skill-a" in result.error or "skill-b" in result.error


# ── no command — shows skill info ─────────────────────────────


class TestNoCommand:
    @pytest.mark.asyncio
    async def test_no_command_returns_info(self):
        skill = _make_skill("my-skill")
        index = {"my-skill": skill}
        with patch("navig.skills.loader.skills_by_id", return_value=index):
            result = await _tool().run({"skill_id": "my-skill"})
        assert result.success is True
        assert "info" in result.output

    @pytest.mark.asyncio
    async def test_info_contains_skill_name(self):
        skill = _make_skill("my-skill")
        index = {"my-skill": skill}
        with patch("navig.skills.loader.skills_by_id", return_value=index):
            result = await _tool().run({"skill_id": "my-skill"})
        assert "my-skill" in result.output["info"]

    @pytest.mark.asyncio
    async def test_no_command_no_skill_fallback_fails(self):
        """When loader fails and command is empty, return error."""
        with patch("navig.skills.loader.skills_by_id", side_effect=Exception("loader broke")):
            result = await _tool().run({"skill_id": "my-skill"})
        assert result.success is False


# ── successful execution ──────────────────────────────────────


class TestSuccessfulExecution:
    @pytest.mark.asyncio
    async def test_success_result(self):
        skill = _make_skill("my-skill")
        index = {"my-skill": skill}
        proc_result = _make_proc_result(stdout="hello world")

        with patch("navig.skills.loader.skills_by_id", return_value=index):
            with patch("navig.tools.skill_runner.run_process", AsyncMock(return_value=proc_result)):
                with patch("navig.tools.skill_runner._find_navig_bin", return_value=["navig"]):
                    result = await _tool().run({"skill_id": "my-skill", "command": "summary"})

        assert result.success is True
        assert result.output["output"] == "hello world"
        assert result.output["skill_id"] == "my-skill"
        assert result.output["command"] == "summary"

    @pytest.mark.asyncio
    async def test_stderr_appended_to_output(self):
        skill = _make_skill("my-skill")
        index = {"my-skill": skill}
        proc_result = _make_proc_result(stdout="out", stderr="err")

        with patch("navig.skills.loader.skills_by_id", return_value=index):
            with patch("navig.tools.skill_runner.run_process", AsyncMock(return_value=proc_result)):
                with patch("navig.tools.skill_runner._find_navig_bin", return_value=["navig"]):
                    result = await _tool().run({"skill_id": "my-skill", "command": "run"})

        assert "out" in result.output["output"]
        assert "err" in result.output["output"]


# ── failed execution ──────────────────────────────────────────


class TestFailedExecution:
    @pytest.mark.asyncio
    async def test_nonzero_exit_fails(self):
        skill = _make_skill("my-skill")
        index = {"my-skill": skill}
        proc_result = _make_proc_result(returncode=1, stdout="failed")

        with patch("navig.skills.loader.skills_by_id", return_value=index):
            with patch("navig.tools.skill_runner.run_process", AsyncMock(return_value=proc_result)):
                with patch("navig.tools.skill_runner._find_navig_bin", return_value=["navig"]):
                    result = await _tool().run({"skill_id": "my-skill", "command": "run"})

        assert result.success is False
        assert "1" in result.error

    @pytest.mark.asyncio
    async def test_timeout_termination_fails(self):
        skill = _make_skill("my-skill")
        index = {"my-skill": skill}
        proc_result = _make_proc_result(returncode=0, termination="timeout")

        with patch("navig.skills.loader.skills_by_id", return_value=index):
            with patch("navig.tools.skill_runner.run_process", AsyncMock(return_value=proc_result)):
                with patch("navig.tools.skill_runner._find_navig_bin", return_value=["navig"]):
                    result = await _tool().run({"skill_id": "my-skill", "command": "run"})

        assert result.success is False
        assert "timeout" in result.error.lower()


# ── _find_navig_bin ───────────────────────────────────────────


class TestFindNavigBin:
    def test_returns_list(self):
        result = _find_navig_bin()
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_when_navig_found_in_path(self):
        with patch("shutil.which", return_value="/usr/local/bin/navig"):
            result = _find_navig_bin()
        assert result == ["/usr/local/bin/navig"]

    def test_when_navig_not_in_path_uses_python(self):
        with patch("shutil.which", return_value=None):
            result = _find_navig_bin()
        assert sys.executable in result
        assert "-m" in result
        assert "navig" in result
