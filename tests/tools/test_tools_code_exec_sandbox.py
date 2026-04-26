"""Tests for navig.tools.code_exec_sandbox — CodeExecSandboxTool."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.tools.code_exec_sandbox import CodeExecSandboxTool


@pytest.fixture
def tool():
    return CodeExecSandboxTool()


class TestCodeExecSandboxTool:
    def test_name(self, tool):
        assert tool.name == "code_exec_sandbox"

    def test_owner_only(self, tool):
        assert tool.owner_only is True

    def test_has_code_parameter(self, tool):
        param_names = [p["name"] for p in tool.parameters]
        assert "code" in param_names


class TestCodeExecRun:
    @pytest.fixture
    def tool(self):
        return CodeExecSandboxTool()

    async def test_empty_code_returns_error(self, tool):
        result = await tool.run({"code": ""})
        assert result.success is False
        assert "code arg required" in (result.error or "")

    async def test_unsupported_language_returns_error(self, tool):
        result = await tool.run({"code": "print(1)", "language": "javascript"})
        assert result.success is False
        assert "not supported" in (result.error or "")

    async def test_successful_execution(self, tool):
        result = await tool.run({"code": "print('hello')"})
        assert result.success is True
        assert result.output is not None
        assert "hello" in result.output.get("stdout", "")

    async def test_exit_code_in_output(self, tool):
        result = await tool.run({"code": "print('ok')"})
        assert result.output["exit_code"] == 0

    async def test_failing_code_not_success(self, tool):
        result = await tool.run({"code": "raise ValueError('boom')"})
        assert result.success is False
        assert result.output["exit_code"] != 0

    async def test_stderr_captured(self, tool):
        result = await tool.run({"code": "import sys; sys.stderr.write('err_msg'); sys.exit(1)"})
        assert "err_msg" in result.output.get("stderr", "")

    async def test_timeout_returns_error(self, tool):
        proc_mock = AsyncMock()
        # communicate() returns (b"", b"") on the second call (after kill)
        proc_mock.communicate = AsyncMock(return_value=(b"", b""))
        proc_mock.kill = MagicMock()
        proc_mock.returncode = -1

        with patch("navig.tools.code_exec_sandbox.asyncio.create_subprocess_exec", return_value=proc_mock):
            with patch("navig.tools.code_exec_sandbox.asyncio.wait_for", side_effect=asyncio.TimeoutError):
                result = await tool.run({"code": "import time; time.sleep(100)"})
        assert result.success is False
        assert "timed out" in (result.error or "")
