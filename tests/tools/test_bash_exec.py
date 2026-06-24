"""
Tests for navig.tools.bash_exec — BashExecTool safe shell execution.
"""
import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.tools.bash_exec import ApprovalRequiredError, BashExecTool, _DEFAULT_TIMEOUT


def _run(coro):
    return asyncio.run(coro)


tool = BashExecTool()


# ---------------------------------------------------------------------------
# Static attributes
# ---------------------------------------------------------------------------

class TestBashExecToolAttributes:
    def test_name(self):
        assert tool.name == "bash_exec"

    def test_owner_only(self):
        assert tool.owner_only is True

    def test_description_mentions_shell(self):
        assert "shell" in tool.description.lower() or "command" in tool.description.lower()

    def test_parameters_list(self):
        names = {p["name"] for p in tool.parameters}
        assert "command" in names

    def test_command_required(self):
        cmd_param = next(p for p in tool.parameters if p["name"] == "command")
        assert cmd_param["required"] is True


# ---------------------------------------------------------------------------
# _build_env
# ---------------------------------------------------------------------------

class TestBuildEnv:
    def test_returns_copy_of_os_environ_when_no_extra(self):
        env = tool._build_env(None)
        assert isinstance(env, dict)
        assert "PATH" in env or len(env) > 0

    def test_extra_keys_merged(self):
        env = tool._build_env({"MY_CUSTOM_VAR": "yes"})
        assert env["MY_CUSTOM_VAR"] == "yes"

    def test_extra_keys_override(self):
        orig = os.environ.get("PATH", "original")
        env = tool._build_env({"PATH": "/custom"})
        assert env["PATH"] == "/custom"

    def test_no_extra_does_not_mutate_os_environ(self):
        env = tool._build_env(None)
        env["_TEST_MUTATION"] = "mutated"
        assert "_TEST_MUTATION" not in os.environ


# ---------------------------------------------------------------------------
# run — validation errors
# ---------------------------------------------------------------------------

class TestBashExecRunValidation:
    def test_empty_args_missing_command(self):
        result = _run(tool.run({}))
        assert result.success is False
        assert "command" in result.error.lower()

    def test_empty_command_string(self):
        result = _run(tool.run({"command": ""}))
        assert result.success is False

    def test_whitespace_only_command(self):
        result = _run(tool.run({"command": "   "}))
        assert result.success is False

    def test_unclosed_quote_returns_parse_error(self):
        # shlex.split raises ValueError for unclosed quotes
        result = _run(tool.run({"command": "'unclosed"}))
        assert result.success is False
        assert "parse" in result.error.lower()

    def test_requires_approval_without_env_blocked(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NAVIG_ALLOW_ALL_COMMANDS", None)
            result = _run(tool.run({"command": "echo hi", "requires_approval": True}))
        assert result.success is False
        assert "approval" in result.error.lower()

    def test_requires_approval_allowed_when_env_set(self):
        with patch.dict(os.environ, {"NAVIG_ALLOW_ALL_COMMANDS": "true"}):
            result = _run(tool.run({"command": "echo allowed", "requires_approval": True}))
        # On Windows may not have 'echo' as a standalone; just check it doesn't block
        # on approval — either succeeds or fails with a different error
        assert "approval" not in (result.error or "").lower()


# ---------------------------------------------------------------------------
# run — execution  (real subprocess)
# ---------------------------------------------------------------------------

class TestBashExecRunExecution:
    def test_successful_echo_command(self):
        if os.name == "nt":
            cmd = "cmd /c echo hello"
        else:
            cmd = "echo hello"
        result = _run(tool.run({"command": cmd}))
        assert result.success is True
        assert "hello" in result.output.lower()

    def test_exit_code_nonzero_failure(self):
        if os.name == "nt":
            cmd = "cmd /c exit 1"
        else:
            cmd = "sh -c 'exit 1'"
        result = _run(tool.run({"command": cmd}))
        assert result.success is False
        assert "non-zero" in result.error.lower()

    def test_executable_not_found(self):
        result = _run(tool.run({"command": "_navig_nonexistent_command_xyz"}))
        assert result.success is False
        assert "not found" in result.error.lower() or "executable" in result.error.lower()

    def test_output_truncated_at_max_output(self):
        # Produce 100 chars of output, but limit to 50
        if os.name == "nt":
            cmd = "cmd /c echo " + ("x" * 100)
        else:
            cmd = "echo " + ("x" * 100)
        result = _run(tool.run({"command": cmd, "max_output": 50}))
        assert result.output is not None
        assert "truncated" in result.output.lower() or len(result.output) <= 60

    def test_elapsed_ms_positive(self):
        if os.name == "nt":
            cmd = "cmd /c echo hi"
        else:
            cmd = "echo hi"
        result = _run(tool.run({"command": cmd}))
        assert result.elapsed_ms >= 0

    def test_status_events_populated(self):
        if os.name == "nt":
            cmd = "cmd /c echo status"
        else:
            cmd = "echo status"
        result = _run(tool.run({"command": cmd}))
        assert len(result.status_events) >= 1

    def test_on_status_callback_called(self):
        events = []
        if os.name == "nt":
            cmd = "cmd /c echo hi"
        else:
            cmd = "echo hi"
        _run(tool.run({"command": cmd}, on_status=events.append))
        assert len(events) >= 1

    def test_env_extra_passed_to_subprocess(self):
        if os.name == "nt":
            cmd = "cmd /c echo %MY_TEST_VAR%"
        else:
            cmd = "sh -c 'echo $MY_TEST_VAR'"
        result = _run(tool.run({"command": cmd, "env_extra": {"MY_TEST_VAR": "navig_ok"}}))
        assert result.success is True
        assert "navig_ok" in result.output
