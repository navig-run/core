"""Tests for navig/tools/domains/exec_pack.py"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.tools.domains.exec_pack import (
    _OUTPUT_CAP,
    _run_shell,
    bash_exec_async_handler,
    bash_exec_handler,
    register_tools,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestOutputCap:
    def test_cap_value(self):
        assert _OUTPUT_CAP == 50_000

    def test_cap_is_int(self):
        assert isinstance(_OUTPUT_CAP, int)


# ---------------------------------------------------------------------------
# _run_shell (async)
# ---------------------------------------------------------------------------


def _make_process_result(
    stdout="out",
    stderr="err",
    returncode=0,
    termination="normal",
    truncated=False,
    elapsed_ms=100,
):
    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    r.termination = termination
    r.truncated = truncated
    r.elapsed_ms = elapsed_ms
    return r


class TestRunShell:
    def test_returns_dict_with_all_keys(self):
        mock_result = _make_process_result()
        with patch(
            "navig.tools.domains.exec_pack.run_process",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = asyncio.run(
                _run_shell("echo hi")
            )
        assert {"stdout", "stderr", "returncode", "timed_out", "truncated", "elapsed_ms", "termination"} <= result.keys()

    def test_timed_out_true_when_timeout_termination(self):
        mock_result = _make_process_result(termination="timeout")
        with patch(
            "navig.tools.domains.exec_pack.run_process",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = asyncio.run(
                _run_shell("sleep 100", timeout_seconds=0.001)
            )
        assert result["timed_out"] is True

    def test_timed_out_false_for_normal_termination(self):
        mock_result = _make_process_result(termination="normal")
        with patch(
            "navig.tools.domains.exec_pack.run_process",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = asyncio.run(
                _run_shell("echo hi")
            )
        assert result["timed_out"] is False

    def test_no_output_timeout_is_also_timed_out(self):
        mock_result = _make_process_result(termination="no_output_timeout")
        with patch(
            "navig.tools.domains.exec_pack.run_process",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = asyncio.run(
                _run_shell("echo hi")
            )
        assert result["timed_out"] is True

    def test_stdout_propagated(self):
        mock_result = _make_process_result(stdout="hello world")
        with patch(
            "navig.tools.domains.exec_pack.run_process",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = asyncio.run(
                _run_shell("echo hello world")
            )
        assert result["stdout"] == "hello world"

    def test_returncode_propagated(self):
        mock_result = _make_process_result(returncode=42)
        with patch(
            "navig.tools.domains.exec_pack.run_process",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = asyncio.run(
                _run_shell("exit 42")
            )
        assert result["returncode"] == 42

    def test_truncated_propagated(self):
        mock_result = _make_process_result(truncated=True)
        with patch(
            "navig.tools.domains.exec_pack.run_process",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = asyncio.run(
                _run_shell("very long command")
            )
        assert result["truncated"] is True

    def test_passes_output_cap_to_process_options(self):
        mock_result = _make_process_result()
        captured_options = []
        async def fake_run_process(argv, opts):
            captured_options.append(opts)
            return mock_result

        with patch("navig.tools.domains.exec_pack.run_process", side_effect=fake_run_process):
            asyncio.run(_run_shell("echo hi"))
        assert captured_options[0].output_cap == _OUTPUT_CAP


# ---------------------------------------------------------------------------
# bash_exec_handler (sync)
# ---------------------------------------------------------------------------


class TestBashExecHandler:
    def test_returns_dict_with_all_keys(self):
        mock_result = _make_process_result()
        with patch(
            "navig.tools.proc.run_process_sync",
            return_value=mock_result,
        ):
            result = bash_exec_handler(command="ls")
        assert {"stdout", "stderr", "returncode", "timed_out", "truncated", "elapsed_ms", "termination"} <= result.keys()

    def test_timeout_termination_maps_to_timed_out(self):
        mock_result = _make_process_result(termination="timeout")
        with patch(
            "navig.tools.proc.run_process_sync",
            return_value=mock_result,
        ):
            result = bash_exec_handler(command="sleep 1")
        assert result["timed_out"] is True

    def test_normal_termination_not_timed_out(self):
        mock_result = _make_process_result(termination="normal")
        with patch(
            "navig.tools.proc.run_process_sync",
            return_value=mock_result,
        ):
            result = bash_exec_handler(command="echo ok")
        assert result["timed_out"] is False

    def test_extra_kwargs_ignored(self):
        mock_result = _make_process_result()
        with patch(
            "navig.tools.proc.run_process_sync",
            return_value=mock_result,
        ):
            result = bash_exec_handler(command="ls", unknown_flag=True, something_else="x")
        assert result["returncode"] == 0

    def test_cwd_passed_through(self):
        mock_result = _make_process_result()
        captured = []
        def fake_sync(argv, opts):
            captured.append(opts)
            return mock_result

        with patch("navig.tools.proc.run_process_sync", side_effect=fake_sync):
            bash_exec_handler(command="ls", cwd="/tmp")
        assert captured[0].cwd == "/tmp"

    def test_env_extra_passed_through(self):
        mock_result = _make_process_result()
        captured = []
        def fake_sync(argv, opts):
            captured.append(opts)
            return mock_result

        with patch("navig.tools.proc.run_process_sync", side_effect=fake_sync):
            bash_exec_handler(command="ls", env_extra={"MY_VAR": "x"})
        assert captured[0].env_extra == {"MY_VAR": "x"}


# ---------------------------------------------------------------------------
# bash_exec_async_handler
# ---------------------------------------------------------------------------


class TestBashExecAsyncHandler:
    def test_returns_dict(self):
        mock_result = _make_process_result()
        with patch(
            "navig.tools.domains.exec_pack.run_process",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = asyncio.run(
                bash_exec_async_handler(command="echo hi")
            )
        assert isinstance(result, dict)

    def test_extra_kwargs_ignored(self):
        mock_result = _make_process_result()
        with patch(
            "navig.tools.domains.exec_pack.run_process",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = asyncio.run(
                bash_exec_async_handler(command="echo hi", unknown="x")
            )
        assert "returncode" in result


# ---------------------------------------------------------------------------
# register_tools
# ---------------------------------------------------------------------------


class TestRegisterTools:
    def test_registers_bash_exec(self):
        mock_registry = MagicMock()
        with patch(
            "navig.tools.router.SafetyLevel", create=True
        ), patch(
            "navig.tools.router.ToolDomain", create=True
        ), patch(
            "navig.tools.router.ToolMeta", return_value=MagicMock()
        ):
            register_tools(mock_registry)
        mock_registry.register.assert_called_once()

    def test_registers_with_async_handler(self):
        mock_registry = MagicMock()
        with patch(
            "navig.tools.router.SafetyLevel", create=True
        ), patch(
            "navig.tools.router.ToolDomain", create=True
        ), patch(
            "navig.tools.router.ToolMeta", return_value=MagicMock()
        ):
            register_tools(mock_registry)
        _, kwargs = mock_registry.register.call_args
        assert kwargs["handler"] is bash_exec_async_handler
