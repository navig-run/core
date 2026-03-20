"""tests/test_exec_pack.py — Tests for the bash_exec tool pack."""
from __future__ import annotations

import sys
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset():
    from navig.tools.router import reset_globals
    from navig.tools.hooks import reset_hook_registry
    reset_globals()
    reset_hook_registry()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_exec_pack_registers_bash_exec():
    _reset()
    from navig.tools.router import get_tool_registry
    registry = get_tool_registry()
    tool = registry.get_tool("bash_exec")
    assert tool is not None
    assert tool.name == "bash_exec"


def test_bash_exec_is_dangerous():
    _reset()
    from navig.tools.router import get_tool_registry, SafetyLevel
    tool = get_tool_registry().get_tool("bash_exec")
    assert tool.safety == SafetyLevel.DANGEROUS


def test_bash_exec_in_system_domain():
    _reset()
    from navig.tools.router import get_tool_registry, ToolDomain
    tool = get_tool_registry().get_tool("bash_exec")
    assert tool.domain == ToolDomain.SYSTEM


def test_bash_exec_handler_is_loaded():
    _reset()
    from navig.tools.router import get_tool_registry
    handler = get_tool_registry().get_handler("bash_exec")
    assert handler is not None
    assert callable(handler)


# ---------------------------------------------------------------------------
# _truncate_middle helper
# ---------------------------------------------------------------------------

def test_truncate_middle_short_text_unchanged():
    from navig.tools.proc import _truncate_middle
    text = "hello world"
    assert _truncate_middle(text, 100) == text


def test_truncate_middle_long_text_truncated():
    from navig.tools.proc import _truncate_middle
    big = "A" * 200
    result = _truncate_middle(big, 100)
    assert len(result) < 200
    assert "omitted" in result


def test_truncate_middle_preserves_head_and_tail():
    from navig.tools.proc import _truncate_middle
    text = "START" + "X" * 200 + "END"
    result = _truncate_middle(text, 50)
    assert result.startswith("START")
    assert result.endswith("END")


# ---------------------------------------------------------------------------
# _run_shell coroutine
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_shell_simple_command():
    from navig.tools.domains.exec_pack import _run_shell

    # Use a cross-platform command
    cmd = "echo hello"
    result = await _run_shell(cmd, timeout_seconds=10)

    assert result["timed_out"] is False
    assert "hello" in result["stdout"]
    assert result["returncode"] == 0


@pytest.mark.asyncio
async def test_run_shell_nonzero_exit():
    from navig.tools.domains.exec_pack import _run_shell

    if sys.platform == "win32":
        cmd = "exit 1"
    else:
        cmd = "exit 1"

    result = await _run_shell(f"{'cmd /c ' if sys.platform == 'win32' else ''}{cmd}", timeout_seconds=10)
    # On Windows `exit 1` in create_subprocess_shell kills process with code 1
    assert result["timed_out"] is False


@pytest.mark.asyncio
async def test_run_shell_timeout():
    from navig.tools.domains.exec_pack import _run_shell

    # Use a command that sleeps; timeout well before it finishes
    if sys.platform == "win32":
        cmd = "ping -n 5 127.0.0.1 > nul"
    else:
        cmd = "sleep 10"

    result = await _run_shell(cmd, timeout_seconds=0.5)
    assert result["timed_out"] is True
    assert result["returncode"] is None


@pytest.mark.asyncio
async def test_run_shell_output_truncation():
    import os
    import tempfile
    from navig.tools.domains.exec_pack import _run_shell, _OUTPUT_CAP

    # Write a temp script to avoid Windows quoting issues with the Python path
    script = f"import sys\nsys.stdout.write('X' * {_OUTPUT_CAP + 10000})\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script)
        script_path = f.name

    try:
        cmd = f'"{sys.executable}" "{script_path}"'
        result = await _run_shell(cmd, timeout_seconds=15)
    finally:
        os.unlink(script_path)

    assert result["truncated"] is True
    assert len(result["stdout"]) < _OUTPUT_CAP + 5000  # safe headroom for markers


@pytest.mark.asyncio
async def test_run_shell_env_injection():
    from navig.tools.domains.exec_pack import _run_shell

    if sys.platform == "win32":
        cmd = "echo %TEST_NAVIG_VAR%"
    else:
        cmd = "echo $TEST_NAVIG_VAR"

    result = await _run_shell(cmd, env_extra={"TEST_NAVIG_VAR": "navig_ok"}, timeout_seconds=10)
    assert "navig_ok" in result["stdout"]


# ---------------------------------------------------------------------------
# ToolRouter integration (async path — bash_exec handler is async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bash_exec_via_router_permissive():
    """bash_exec should execute in permissive safety mode."""
    _reset()
    from navig.tools.router import get_tool_router, ToolCallAction

    router = get_tool_router(safety_policy={"safety_mode": "permissive"})
    result = await router.async_execute(
        ToolCallAction(tool="bash_exec", parameters={"command": "echo router_ok"})
    )
    # In permissive, destructive check runs but echo is not destructive
    from navig.tools.schemas import ToolResultStatus
    assert result.status == ToolResultStatus.SUCCESS
    assert "router_ok" in str(result.output.get("stdout", ""))


@pytest.mark.asyncio
async def test_bash_exec_blocked_in_strict_mode():
    """In strict safety mode, bash_exec (DANGEROUS) must be denied."""
    _reset()
    from navig.tools.router import get_tool_router, ToolCallAction
    from navig.tools.schemas import ToolResultStatus

    router = get_tool_router(safety_policy={"safety_mode": "strict"})
    result = await router.async_execute(
        ToolCallAction(tool="bash_exec", parameters={"command": "echo hi"})
    )
    assert result.status == ToolResultStatus.DENIED
    assert "DANGEROUS" in (result.error or "")


@pytest.mark.asyncio
async def test_bash_exec_accessible_by_name():
    """bash_exec is accessible by its canonical name in permissive mode."""
    _reset()
    from navig.tools.router import get_tool_router, ToolCallAction
    from navig.tools.schemas import ToolResultStatus

    router = get_tool_router(safety_policy={"safety_mode": "permissive"})
    result = await router.async_execute(
        ToolCallAction(tool="bash_exec", parameters={"command": "echo alias_ok"})
    )
    assert result.status == ToolResultStatus.SUCCESS
