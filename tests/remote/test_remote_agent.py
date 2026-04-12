"""Tests for navig.agent.remote_agent and navig.agent.tools.remote_tools."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.agent.remote_agent import (
    COMMAND_TIMEOUT,
    MAX_OUTPUT_CHARS,
    CommandState,
    RemoteAgentExecutor,
    RemoteCommand,
    RemoteResult,
    RemoteTask,
    _build_navig_command,
    _needs_b64,
    _truncate,
)

pytestmark = pytest.mark.integration

# ═════════════════════════════════════════════════════════════
# Helper: fake subprocess
# ═════════════════════════════════════════════════════════════


def _make_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Return a mock async subprocess with preset communicate() output."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    proc.returncode = returncode
    return proc


# ═════════════════════════════════════════════════════════════
# _needs_b64
# ═════════════════════════════════════════════════════════════


class TestNeedsB64:
    def test_plain_command_no_b64(self):
        assert _needs_b64("ls -la /var/www") is False

    def test_dollar_triggers_b64(self):
        assert _needs_b64("echo $HOME") is True

    def test_backtick_triggers_b64(self):
        assert _needs_b64("echo `date`") is True

    def test_quotes_trigger_b64(self):
        assert _needs_b64("php artisan tinker --execute='echo 1;'") is True

    def test_pipe_triggers_b64(self):
        assert _needs_b64("cat file | grep foo") is True

    def test_empty_string_no_b64(self):
        assert _needs_b64("") is False


# ═════════════════════════════════════════════════════════════
# _build_navig_command
# ═════════════════════════════════════════════════════════════


class TestBuildNavigCommand:
    def test_simple_command(self):
        result = _build_navig_command("ls -la")
        assert result == 'navig run --yes "ls -la"'

    def test_with_host(self):
        result = _build_navig_command("ls", host="prod")
        assert "navig run --host prod --yes" in result

    def test_b64_encoding(self):
        result = _build_navig_command("echo $HOME", use_b64=True)
        assert "--b64" in result
        # Should not contain the raw command
        assert "echo $HOME" not in result

    def test_b64_without_host(self):
        result = _build_navig_command("cmd", use_b64=True)
        parts = result.split()
        assert parts[0] == "navig"
        assert parts[1] == "run"
        assert "--yes" in parts
        assert "--b64" in parts


# ═════════════════════════════════════════════════════════════
# _truncate
# ═════════════════════════════════════════════════════════════


class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello", 100) == "hello"

    def test_long_text_truncated(self):
        text = "x" * 200
        result = _truncate(text, 50)
        assert len(result) < 200
        assert result.startswith("x" * 50)
        assert "truncated" in result

    def test_at_limit_unchanged(self):
        text = "x" * 100
        assert _truncate(text, 100) == text


# ═════════════════════════════════════════════════════════════
# Data structures
# ═════════════════════════════════════════════════════════════


class TestDataStructures:
    def test_remote_command_defaults(self):
        cmd = RemoteCommand(command="ls")
        assert cmd.host is None
        assert cmd.use_b64 is False
        assert cmd.timeout == COMMAND_TIMEOUT
        assert cmd.description == ""

    def test_remote_command_to_dict(self):
        cmd = RemoteCommand(command="ls", host="prod", description="listing")
        d = cmd.to_dict()
        assert d["command"] == "ls"
        assert d["host"] == "prod"
        assert d["description"] == "listing"

    def test_remote_result_success(self):
        r = RemoteResult(host="prod", state=CommandState.COMPLETED, return_code=0)
        assert r.success is True

    def test_remote_result_failed_state(self):
        r = RemoteResult(host="prod", state=CommandState.FAILED, return_code=0)
        assert r.success is False

    def test_remote_result_nonzero_exit(self):
        r = RemoteResult(host="prod", state=CommandState.COMPLETED, return_code=1)
        assert r.success is False

    def test_remote_result_output_merged(self):
        r = RemoteResult(host="h", state=CommandState.COMPLETED, stdout="out", stderr="err")
        assert "out" in r.output
        assert "[stderr] err" in r.output

    def test_remote_result_output_empty(self):
        r = RemoteResult(host="h", state=CommandState.COMPLETED)
        assert r.output == "(no output)"

    def test_remote_result_to_dict(self):
        r = RemoteResult(host="h", state=CommandState.COMPLETED, return_code=0, elapsed_s=1.234)
        d = r.to_dict()
        assert d["host"] == "h"
        assert d["state"] == "completed"
        assert d["elapsed_s"] == 1.23
        assert d["success"] is True

    def test_remote_task_success(self):
        t = RemoteTask(
            host="prod",
            results=[
                RemoteResult(host="prod", state=CommandState.COMPLETED, return_code=0),
                RemoteResult(host="prod", state=CommandState.COMPLETED, return_code=0),
            ],
        )
        assert t.success is True
        assert "2/2" in t.summary

    def test_remote_task_partial_failure(self):
        t = RemoteTask(
            host="prod",
            results=[
                RemoteResult(host="prod", state=CommandState.COMPLETED, return_code=0),
                RemoteResult(host="prod", state=CommandState.FAILED, return_code=1),
            ],
        )
        assert t.success is False
        assert "1/2" in t.summary

    def test_remote_task_to_dict(self):
        t = RemoteTask(host="prod", commands=[RemoteCommand(command="ls")])
        d = t.to_dict()
        assert d["host"] == "prod"
        assert len(d["commands"]) == 1


# ═════════════════════════════════════════════════════════════
# RemoteAgentExecutor — execute_command
# ═════════════════════════════════════════════════════════════


class TestExecuteCommand:
    async def test_successful_command(self):
        proc = _make_proc(stdout="file1\nfile2\n", returncode=0)
        with patch("navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc):
            executor = RemoteAgentExecutor()
            result = await executor.execute_command("ls")
            assert result.success is True
            assert "file1" in result.stdout
            assert result.return_code == 0

    async def test_failed_command(self):
        proc = _make_proc(stderr="not found", returncode=127)
        with patch("navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc):
            executor = RemoteAgentExecutor()
            result = await executor.execute_command("badcmd")
            assert result.success is False
            assert result.return_code == 127

    async def test_timeout_handling(self):
        proc = AsyncMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        with patch("navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc):
            executor = RemoteAgentExecutor()
            result = await executor.execute_command("sleep 999", timeout=1)
            assert result.state == CommandState.TIMED_OUT
            assert result.success is False
            assert "Timed out" in (result.error or "")

    async def test_exception_handling(self):
        with patch(
            "navig.agent.remote_agent.asyncio.create_subprocess_shell",
            side_effect=OSError("spawn failed"),
        ):
            executor = RemoteAgentExecutor()
            result = await executor.execute_command("ls")
            assert result.state == CommandState.FAILED
            assert "spawn failed" in (result.error or "")

    async def test_auto_b64_for_special_chars(self):
        proc = _make_proc(stdout="ok", returncode=0)
        with patch(
            "navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc
        ) as mock_sub:
            executor = RemoteAgentExecutor()
            await executor.execute_command("echo $HOME")
            # Check that --b64 was passed
            call_cmd = mock_sub.call_args[0][0]
            assert "--b64" in call_cmd

    async def test_host_parameter_passed(self):
        proc = _make_proc(returncode=0)
        with patch(
            "navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc
        ) as mock_sub:
            executor = RemoteAgentExecutor()
            await executor.execute_command("ls", host="staging")
            call_cmd = mock_sub.call_args[0][0]
            assert "--host staging" in call_cmd


# ═════════════════════════════════════════════════════════════
# RemoteAgentExecutor — execute_task
# ═════════════════════════════════════════════════════════════


class TestExecuteTask:
    async def test_sequential_all_pass(self):
        proc = _make_proc(stdout="ok", returncode=0)
        with patch("navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc):
            executor = RemoteAgentExecutor()
            task = RemoteTask(
                host="prod",
                commands=[
                    RemoteCommand(command="cmd1"),
                    RemoteCommand(command="cmd2"),
                ],
            )
            result = await executor.execute_task(task)
            assert result.success is True
            assert len(result.results) == 2

    async def test_stop_on_error(self):
        calls = [0]

        async def side_effect(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                return _make_proc(returncode=1, stderr="fail")
            return _make_proc(returncode=0)

        with patch(
            "navig.agent.remote_agent.asyncio.create_subprocess_shell", side_effect=side_effect
        ):
            executor = RemoteAgentExecutor()
            task = RemoteTask(
                host="prod",
                commands=[
                    RemoteCommand(command="fail_cmd"),
                    RemoteCommand(command="never_runs"),
                ],
                stop_on_error=True,
            )
            result = await executor.execute_task(task)
            assert result.success is False
            # Should have stopped after first failure
            assert len(result.results) == 1

    async def test_continue_on_error(self):
        calls = [0]

        async def side_effect(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                return _make_proc(returncode=1)
            return _make_proc(returncode=0)

        with patch(
            "navig.agent.remote_agent.asyncio.create_subprocess_shell", side_effect=side_effect
        ):
            executor = RemoteAgentExecutor()
            task = RemoteTask(
                host="prod",
                commands=[
                    RemoteCommand(command="fail_cmd"),
                    RemoteCommand(command="still_runs"),
                ],
                stop_on_error=False,
            )
            result = await executor.execute_task(task)
            # Both commands should have run
            assert len(result.results) == 2


# ═════════════════════════════════════════════════════════════
# RemoteAgentExecutor — execute_parallel
# ═════════════════════════════════════════════════════════════


class TestExecuteParallel:
    async def test_multi_host_parallel(self):
        proc = _make_proc(stdout="ok", returncode=0)
        with patch("navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc):
            executor = RemoteAgentExecutor()
            commands = [
                RemoteCommand(command="df -h", host="web1"),
                RemoteCommand(command="df -h", host="web2"),
                RemoteCommand(command="df -h", host="web3"),
            ]
            results = await executor.execute_parallel(commands)
            assert len(results) == 3
            assert all(r.success for r in results)

    async def test_parallel_partial_failure(self):
        calls = [0]

        async def side_effect(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 2:
                return _make_proc(returncode=1, stderr="disk full")
            return _make_proc(returncode=0)

        with patch(
            "navig.agent.remote_agent.asyncio.create_subprocess_shell", side_effect=side_effect
        ):
            executor = RemoteAgentExecutor()
            commands = [
                RemoteCommand(command="ls", host="h1"),
                RemoteCommand(command="ls", host="h2"),
                RemoteCommand(command="ls", host="h3"),
            ]
            results = await executor.execute_parallel(commands)
            assert len(results) == 3
            # At least one should have failed
            failed = [r for r in results if not r.success]
            assert len(failed) >= 1

    async def test_parallel_exception_converted(self):
        async def side_effect(*args, **kwargs):
            raise OSError("connection refused")

        with patch(
            "navig.agent.remote_agent.asyncio.create_subprocess_shell", side_effect=side_effect
        ):
            executor = RemoteAgentExecutor()
            commands = [RemoteCommand(command="ls", host="unreachable")]
            results = await executor.execute_parallel(commands)
            assert len(results) == 1
            assert results[0].state == CommandState.FAILED

    async def test_empty_commands_returns_empty(self):
        executor = RemoteAgentExecutor()
        results = await executor.execute_parallel([])
        assert results == []


# ═════════════════════════════════════════════════════════════
# RemoteAgentExecutor — verify_host / set_active_host
# ═════════════════════════════════════════════════════════════


class TestHostManagement:
    async def test_verify_host_success(self):
        proc = _make_proc(stdout="Connection OK", returncode=0)
        with patch("navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc):
            result = await RemoteAgentExecutor.verify_host("prod")
            assert result.success is True
            assert "Connection OK" in result.stdout

    async def test_verify_host_timeout(self):
        proc = AsyncMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        with patch("navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc):
            result = await RemoteAgentExecutor.verify_host("slowhost")
            assert result.state == CommandState.TIMED_OUT

    async def test_set_active_host(self):
        proc = _make_proc(stdout="Switched to prod", returncode=0)
        with patch("navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc):
            result = await RemoteAgentExecutor.set_active_host("prod")
            assert result.success is True

    async def test_set_active_host_timeout(self):
        proc = AsyncMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        with patch("navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc):
            result = await RemoteAgentExecutor.set_active_host("unreachable")
            assert result.state == CommandState.TIMED_OUT


# ═════════════════════════════════════════════════════════════
# Tool classes
# ═════════════════════════════════════════════════════════════


class TestRemoteTools:
    """Test the four BaseTool subclasses from remote_tools.py."""

    @pytest.fixture(autouse=True)
    def _reset_executor(self):
        """Reset the singleton executor between tests."""
        import navig.agent.tools.remote_tools as rt

        rt._executor = None
        yield
        rt._executor = None

    async def test_remote_execute_tool_success(self):
        from navig.agent.tools.remote_tools import RemoteExecuteTool

        proc = _make_proc(stdout="hello", returncode=0)
        with patch("navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc):
            tool = RemoteExecuteTool()
            result = await tool.run({"command": "echo hello"})
            assert result.success is True
            assert "hello" in result.output

    async def test_remote_execute_tool_missing_command(self):
        from navig.agent.tools.remote_tools import RemoteExecuteTool

        tool = RemoteExecuteTool()
        result = await tool.run({})
        assert result.success is False
        assert "required" in result.error.lower()

    async def test_remote_file_read_tool(self):
        from navig.agent.tools.remote_tools import RemoteFileReadTool

        proc = _make_proc(stdout="file content here", returncode=0)
        with patch(
            "navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc
        ) as mock_sub:
            tool = RemoteFileReadTool()
            result = await tool.run({"path": "/var/log/app.log", "tail": True, "lines": "50"})
            assert result.success is True
            # Verify the navig file show command was built
            call_cmd = mock_sub.call_args[0][0]
            assert "navig" in call_cmd

    async def test_remote_file_read_missing_path(self):
        from navig.agent.tools.remote_tools import RemoteFileReadTool

        tool = RemoteFileReadTool()
        result = await tool.run({})
        assert result.success is False

    async def test_remote_host_switch_tool(self):
        from navig.agent.tools.remote_tools import RemoteHostSwitchTool

        proc = _make_proc(stdout="Switched", returncode=0)
        with patch("navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc):
            tool = RemoteHostSwitchTool()
            result = await tool.run({"host": "staging", "verify": False})
            assert result.success is True
            assert "staging" in result.output

    async def test_remote_host_switch_with_verify(self):
        from navig.agent.tools.remote_tools import RemoteHostSwitchTool

        proc = _make_proc(stdout="OK", returncode=0)
        with patch("navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc):
            tool = RemoteHostSwitchTool()
            result = await tool.run({"host": "prod"})
            assert result.success is True
            assert "verified" in result.output.lower()

    async def test_remote_host_switch_missing_host(self):
        from navig.agent.tools.remote_tools import RemoteHostSwitchTool

        tool = RemoteHostSwitchTool()
        result = await tool.run({})
        assert result.success is False

    async def test_remote_multi_host_tool(self):
        from navig.agent.tools.remote_tools import RemoteMultiHostTool

        proc = _make_proc(stdout="disk info", returncode=0)
        with patch("navig.agent.remote_agent.asyncio.create_subprocess_shell", return_value=proc):
            tool = RemoteMultiHostTool()
            result = await tool.run(
                {
                    "command": "df -h",
                    "hosts": ["web1", "web2"],
                }
            )
            assert result.success is True
            assert "web1" in result.output
            assert "web2" in result.output

    async def test_remote_multi_host_missing_hosts(self):
        from navig.agent.tools.remote_tools import RemoteMultiHostTool

        tool = RemoteMultiHostTool()
        result = await tool.run({"command": "ls"})
        assert result.success is False

    async def test_remote_multi_host_empty_hosts(self):
        from navig.agent.tools.remote_tools import RemoteMultiHostTool

        tool = RemoteMultiHostTool()
        result = await tool.run({"command": "ls", "hosts": []})
        assert result.success is False


# ═════════════════════════════════════════════════════════════
# Registration
# ═════════════════════════════════════════════════════════════


class TestRegistration:
    def test_register_remote_executor_tools(self):
        """Smoke test: registration function runs without error."""
        from navig.agent.tools.remote_tools import register_remote_executor_tools

        # Should not raise (idempotent registry)
        register_remote_executor_tools()

    def test_tools_in_toolsets(self):
        """Verify remote tools appear in TOOLSETS and parallel classifications."""
        from navig.agent.toolsets import (
            NEVER_PARALLEL_TOOLS,
            PARALLEL_SAFE_TOOLS,
            TOOLSETS,
        )

        assert "remote" in TOOLSETS
        remote_tools = TOOLSETS["remote"]
        assert "remote_execute" in remote_tools
        assert "remote_file_read" in remote_tools
        assert "remote_host_switch" in remote_tools
        assert "remote_multi_host" in remote_tools

        # Parallel safety
        assert "remote_execute" in PARALLEL_SAFE_TOOLS
        assert "remote_file_read" in PARALLEL_SAFE_TOOLS
        assert "remote_host_switch" in PARALLEL_SAFE_TOOLS
        assert "remote_multi_host" in NEVER_PARALLEL_TOOLS

    def test_tools_in_plan_mode_readonly(self):
        """Verify read-only remote tools appear in plan mode whitelist."""
        from navig.agent.plan_mode import PlanInterceptor

        assert "remote_file_read" in PlanInterceptor.READ_ONLY_TOOLS
        assert "remote_host_switch" in PlanInterceptor.READ_ONLY_TOOLS
        # Mutating tools should NOT be in READ_ONLY
        assert "remote_execute" not in PlanInterceptor.READ_ONLY_TOOLS
        assert "remote_multi_host" not in PlanInterceptor.READ_ONLY_TOOLS
