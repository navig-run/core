"""tests/test_proc.py — Tests for navig.tools.proc canonical subprocess engine.

Covers:
  - ProcessResult typed dataclass (structured output)
  - ProcessOptions and defaults
  - shell_argv platform helper
  - run_process happy path, timeout, structured result fields
  - on_event observable callback (spawn + exit events fire)
  - bridge.try_get_handler graceful degradation
"""

from __future__ import annotations

import sys

import pytest

# ---------------------------------------------------------------------------
# ProcessResult — structured output
# ---------------------------------------------------------------------------


def test_process_result_success_true_on_zero_exit():
    from navig.tools.proc import ProcessResult

    r = ProcessResult(
        stdout="ok", stderr="", returncode=0, pid=1, elapsed_ms=5.0, termination="exit"
    )
    assert r.success() is True


def test_process_result_success_false_on_nonzero():
    from navig.tools.proc import ProcessResult

    r = ProcessResult(
        stdout="", stderr="err", returncode=1, pid=1, elapsed_ms=2.0, termination="exit"
    )
    assert r.success() is False


def test_process_result_success_false_on_timeout():
    from navig.tools.proc import ProcessResult

    r = ProcessResult(
        stdout="",
        stderr="",
        returncode=None,
        pid=1,
        elapsed_ms=30_000.0,
        termination="timeout",
    )
    assert r.success() is False


def test_process_result_to_dict_has_all_keys():
    from navig.tools.proc import ProcessResult

    r = ProcessResult(
        stdout="out",
        stderr="err",
        returncode=0,
        pid=42,
        elapsed_ms=12.3,
        termination="exit",
        truncated=True,
    )
    d = r.to_dict()
    for key in (
        "stdout",
        "stderr",
        "returncode",
        "pid",
        "elapsed_ms",
        "termination",
        "truncated",
    ):
        assert key in d, f"Missing key: {key}"
    assert d["stdout"] == "out"
    assert d["truncated"] is True
    assert d["elapsed_ms"] == pytest.approx(12.3)


# ---------------------------------------------------------------------------
# shell_argv
# ---------------------------------------------------------------------------


def test_shell_argv_posix():
    from navig.tools.proc import shell_argv

    argv = shell_argv("echo hi")
    if sys.platform == "win32":
        assert argv[-1] == "echo hi"
        assert "cmd" in argv[0].lower() or argv[0].lower() == "cmd.exe"
    else:
        assert argv == ["sh", "-c", "echo hi"]


def test_shell_argv_windows_uses_comspec(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("Windows-only test")

    monkeypatch.setenv("ComSpec", "C:\\Windows\\System32\\cmd.exe")
    from navig.tools.proc import shell_argv

    argv = shell_argv("dir")
    assert argv[0] == "C:\\Windows\\System32\\cmd.exe"
    assert argv[1:3] == ["/d", "/s"]


# ---------------------------------------------------------------------------
# run_process — structured result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_process_echo_returns_typed_result():
    from navig.tools.proc import ProcessOptions, run_process, shell_argv

    result = await run_process(
        shell_argv("echo proc_test_ok"),
        ProcessOptions(timeout_s=10.0),
    )
    assert "proc_test_ok" in result.stdout
    assert result.returncode == 0
    assert result.termination == "exit"
    assert result.elapsed_ms > 0
    assert result.pid is not None
    assert result.truncated is False


@pytest.mark.asyncio
async def test_run_process_nonzero_exit_code():
    from navig.tools.proc import ProcessOptions, run_process, shell_argv

    if sys.platform == "win32":
        cmd = "exit 2"
    else:
        cmd = "exit 2"
    result = await run_process(shell_argv(cmd), ProcessOptions(timeout_s=10.0))
    assert result.returncode == 2
    assert result.termination == "exit"


@pytest.mark.asyncio
async def test_run_process_timeout_sets_termination():
    from navig.tools.proc import ProcessOptions, run_process, shell_argv

    if sys.platform == "win32":
        cmd = "ping -n 10 127.0.0.1 > nul"
    else:
        cmd = "sleep 10"

    result = await run_process(shell_argv(cmd), ProcessOptions(timeout_s=0.4))
    assert result.termination == "timeout"
    assert result.returncode is None


@pytest.mark.asyncio
async def test_run_process_structured_output_includes_elapsed_ms():
    from navig.tools.proc import ProcessOptions, run_process, shell_argv

    result = await run_process(
        shell_argv("echo timer_test"), ProcessOptions(timeout_s=10.0)
    )
    assert isinstance(result.elapsed_ms, float)
    assert result.elapsed_ms >= 0.0


@pytest.mark.asyncio
async def test_run_process_env_extra_injected():
    from navig.tools.proc import ProcessOptions, run_process, shell_argv

    if sys.platform == "win32":
        cmd = "echo %NAVIG_PROC_TESTVAR%"
    else:
        cmd = "echo $NAVIG_PROC_TESTVAR"

    result = await run_process(
        shell_argv(cmd),
        ProcessOptions(
            timeout_s=10.0, env_extra={"NAVIG_PROC_TESTVAR": "proc_injected"}
        ),
    )
    assert "proc_injected" in result.stdout


@pytest.mark.asyncio
async def test_run_process_output_cap_truncates():
    from navig.tools.proc import ProcessOptions, run_process

    cap = 500
    # Invoke Python directly (no shell) to avoid cross-platform quote issues
    result = await run_process(
        [sys.executable, "-c", f"print('X' * 2000)"],
        ProcessOptions(timeout_s=15.0, output_cap=cap),
    )
    assert result.truncated is True
    # stdout length should be under cap + marker overhead (\u2248200 chars)
    assert len(result.stdout) < cap + 300


# ---------------------------------------------------------------------------
# Observable execution — on_event callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_event_callback_fires_spawn_and_exit():
    from navig.tools.proc import ProcessOptions, run_process, shell_argv

    events: list[tuple[str, str]] = []

    def capture(event: str, detail: str) -> None:
        events.append((event, detail))

    await run_process(
        shell_argv("echo observable"),
        ProcessOptions(timeout_s=10.0, on_event=capture),
    )

    event_names = [e for e, _ in events]
    assert "spawn" in event_names
    assert "exit" in event_names


@pytest.mark.asyncio
async def test_on_event_callback_fires_timeout_event():
    from navig.tools.proc import ProcessOptions, run_process, shell_argv

    events: list[str] = []

    def capture(event: str, detail: str) -> None:
        events.append(event)

    if sys.platform == "win32":
        cmd = "ping -n 10 127.0.0.1 > nul"
    else:
        cmd = "sleep 10"

    await run_process(shell_argv(cmd), ProcessOptions(timeout_s=0.4, on_event=capture))
    assert "timeout" in events


# ---------------------------------------------------------------------------
# run_process_sync
# ---------------------------------------------------------------------------


def test_run_process_sync_basic():
    from navig.tools.proc import ProcessOptions, run_process_sync, shell_argv

    result = run_process_sync(
        shell_argv("echo sync_ok"), ProcessOptions(timeout_s=10.0)
    )
    assert "sync_ok" in result.stdout
    assert result.returncode == 0
    assert result.termination == "exit"


# ---------------------------------------------------------------------------
# bridge.try_get_handler — graceful degradation
# ---------------------------------------------------------------------------


def test_try_get_handler_returns_none_for_missing_tool():
    from navig.tools.bridge import try_get_handler
    from navig.tools.router import ToolRegistry

    empty = ToolRegistry()
    result = try_get_handler(empty, "nonexistent_tool_xyz")
    assert result is None


def test_try_get_handler_returns_handler_for_registered_tool():
    from navig.tools.bridge import try_get_handler
    from navig.tools.hooks import reset_hook_registry
    from navig.tools.router import get_tool_registry, reset_globals

    reset_globals()
    reset_hook_registry()

    registry = get_tool_registry()
    handler = try_get_handler(registry, "bash_exec")
    assert handler is not None
    assert callable(handler)
