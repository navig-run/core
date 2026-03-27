"""
navig.tools.proc — Canonical subprocess engine for NAVIG tool pipeline.

All subprocess I/O in the tool layer flows through ``run_process``.  No other
module should call ``asyncio.create_subprocess_*`` or ``subprocess.run``
directly for tool execution.

Design goals
────────────
* ``shell=False`` by default — ``create_subprocess_exec`` is used so the OS
  never interprets shell metacharacters in *argv*.
* Shell commands (``bash_exec``) arrive pre-split as ``["sh", "-c", cmd]`` so
  the same safe path is used — callers use ``shell_argv(cmd)`` for this.
* Windows ``.cmd`` / ``.bat`` shim detection prevents the EINVAL that occurs
  when spawn receives a batch-file path without ``cmd.exe`` wrapping.
* Dual-timer guard: wall-clock timeout + optional no-output timeout, both
  SIGKILL on expiry.
* Structured ``ProcessResult`` with elapsed_ms, termination reason, and a
  ``truncated`` flag — callers get typed fields instead of opaque dicts.
* Optional ``on_event`` callback for observable execution (fires at ``spawn``,
  ``stdout``, ``stderr``, ``timeout``, ``exit``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger("navig.tools.proc")

# ── Types ────────────────────────────────────────────────────────────────────

Termination = Literal["exit", "timeout", "no_output_timeout", "signal"]

EventCallback = Callable[[str, str], None]
AsyncEventCallback = Callable[[str, str], Awaitable[None]]
AnyEventCallback = EventCallback | AsyncEventCallback


# ── Options / Result ─────────────────────────────────────────────────────────


@dataclass
class ProcessOptions:
    """Options forwarded to ``run_process``."""

    timeout_s: float = 60.0
    """Wall-clock kill timeout in seconds."""

    no_output_timeout_s: float | None = None
    """Kill if no stdout/stderr activity for this many seconds.  ``None`` disables."""

    cwd: str | None = None
    """Working directory.  None → inherit from current process."""

    env_extra: dict[str, str] | None = None
    """Variables merged on top of ``os.environ`` (None values stripped)."""

    input_data: bytes | None = None
    """Bytes written to stdin.  None → stdin not opened."""

    output_cap: int = 50_000
    """Hard cap on combined stdout + stderr (characters).  0 = unlimited."""

    on_event: AnyEventCallback | None = None
    """Optional callback fired at: spawn / stdout / stderr / timeout / exit."""


@dataclass
class ProcessResult:
    """Structured result from a completed or killed process."""

    stdout: str
    stderr: str
    returncode: int | None
    pid: int | None
    elapsed_ms: float
    termination: Termination
    truncated: bool = False

    def success(self) -> bool:
        return self.returncode == 0 and self.termination == "exit"

    def to_dict(self) -> dict:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
            "pid": self.pid,
            "elapsed_ms": self.elapsed_ms,
            "termination": self.termination,
            "truncated": self.truncated,
        }


# ── Windows helpers ──────────────────────────────────────────────────────────

_WINDOWS_BATCH_EXTS = {".cmd", ".bat"}


def _needs_cmd_wrapper(exe: str) -> bool:
    """Return True if *exe* is a Windows batch file that requires cmd.exe wrapping."""
    if sys.platform != "win32":
        return False
    from pathlib import Path

    return Path(exe).suffix.lower() in _WINDOWS_BATCH_EXTS


def _cmd_wrap_argv(exe: str, args: Sequence[str]) -> list[str]:
    """Wrap a .cmd/.bat binary + args under ``cmd.exe /d /s /c``."""
    comspec = os.environ.get("ComSpec", "cmd.exe")
    # Build a single quoted token list ala buildCmdExeCommandLine
    inner = " ".join([exe] + list(args))
    return [comspec, "/d", "/s", "/c", inner]


# ── Shell helpers ─────────────────────────────────────────────────────────────


def shell_argv(command: str) -> list[str]:
    """
    Return the argv list for running *command* through the system shell.

    On POSIX: ``["sh", "-c", command]``
    On Windows: ``[cmd.exe, "/d", "/s", "/c", command]``

    Using this keeps ``run_process`` in shell=False mode while still
    supporting arbitrary shell syntax in the command string.
    """
    if sys.platform == "win32":
        comspec = os.environ.get("ComSpec", "cmd.exe")
        return [comspec, "/d", "/s", "/c", command]
    return ["sh", "-c", command]


# ── Output truncation ────────────────────────────────────────────────────────


def _truncate_middle(text: str, cap: int) -> str:
    """Proportional middle-trim: keep first half + last half of *cap* chars."""
    if cap <= 0 or len(text) <= cap:
        return text
    keep = cap // 2
    omitted = len(text) - cap
    return (
        text[:keep]
        + f"\n\n[... {omitted:,} characters omitted — output capped at {cap:,} ...]\n\n"
        + text[-keep:]
    )


def _apply_cap(stdout: str, stderr: str, cap: int) -> tuple[str, str, bool]:
    """Trim stdout/stderr proportionally to stay within *cap*.  Returns (out, err, truncated)."""
    if cap <= 0:
        return stdout, stderr, False
    combined = len(stdout) + len(stderr)
    if combined <= cap:
        return stdout, stderr, False
    out_cap = int(cap * (len(stdout) / max(combined, 1))) or cap
    err_cap = cap - out_cap
    return _truncate_middle(stdout, out_cap), _truncate_middle(stderr, err_cap), True


# ── Event dispatch ────────────────────────────────────────────────────────────


async def _emit(cb: AnyEventCallback | None, event: str, detail: str) -> None:
    if cb is None:
        return
    try:
        result = cb(event, detail)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.debug("on_event callback raised", exc_info=True)


# ── Core engine ───────────────────────────────────────────────────────────────


async def run_process(
    argv: list[str],
    opts: ProcessOptions | None = None,
) -> ProcessResult:
    """
    Run *argv* as a subprocess and return a ``ProcessResult``.

    Uses ``create_subprocess_exec`` (shell=False) for safety.  For shell
    syntax pass ``shell_argv(command)`` as *argv*.

    Args:
        argv:  Command + arguments list.  Must be non-empty.
        opts:  Execution options.  ``None`` uses all defaults.

    Returns:
        ``ProcessResult`` with stdout, stderr, returncode, elapsed_ms, etc.

    Raises:
        ValueError: If *argv* is empty.
        OSError / FileNotFoundError: If the executable cannot be found.
    """
    if not argv:
        raise ValueError("run_process: argv must not be empty")

    if opts is None:
        opts = ProcessOptions()

    exe, *args = argv

    # Windows .cmd/.bat shim detection
    if _needs_cmd_wrapper(exe):
        exe, *args = _cmd_wrap_argv(exe, args)

    # Build environment
    env: dict[str, str] | None = None
    if opts.env_extra:
        env = {k: str(v) for k, v in {**os.environ, **opts.env_extra}.items() if v is not None}

    stdin_mode = (
        asyncio.subprocess.PIPE if opts.input_data is not None else asyncio.subprocess.DEVNULL
    )

    t0 = time.monotonic()

    # ── Spawn ─────────────────────────────────────────────────────────────────
    # On Windows, when the resolved exe is cmd.exe (e.g. from shell_argv()), the
    # Windows CreateProcess argument-quoting layer double-escapes the inner
    # command string, mangling paths that contain spaces or quotes.
    # Passing the inner command to create_subprocess_shell avoids this because
    # Python's shell=True path constructs the cmd.exe /c invocation natively.
    _is_win_shell = (
        sys.platform == "win32"
        and os.path.basename(exe).lower() in ("cmd.exe", "cmd")
        and args
        and args[0] in ("/c", "/s", "/d")
    )

    if _is_win_shell:
        # args = ["/d", "/s", "/c", inner_command]  — take the last element
        inner_cmd = args[-1] if args else ""
        proc = await asyncio.create_subprocess_shell(
            inner_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=stdin_mode,
            cwd=opts.cwd,
            env=env,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            exe,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=stdin_mode,
            cwd=opts.cwd,
            env=env,
        )

    pid = proc.pid
    await _emit(opts.on_event, "spawn", str(pid))

    if opts.input_data is not None and proc.stdin:
        proc.stdin.write(opts.input_data)
        try:
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass  # pipe closed during drain; expected
        proc.stdin.close()

    # ── Streaming read with no-output timer ──────────────────────────────────
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    termination: Termination = "exit"
    timed_out = False
    no_output_timed_out = False

    no_output_deadline: float | None = None
    if opts.no_output_timeout_s and opts.no_output_timeout_s > 0:
        no_output_deadline = time.monotonic() + opts.no_output_timeout_s

    async def _read_stream(stream: asyncio.StreamReader, chunks: list[bytes], tag: str) -> None:
        nonlocal no_output_deadline
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if no_output_deadline is not None:
                no_output_deadline = time.monotonic() + opts.no_output_timeout_s  # type: ignore[operator]
            await _emit(opts.on_event, tag, f"{len(chunk)} bytes")

    async def _communicate() -> None:
        tasks = []
        if proc.stdout:
            tasks.append(asyncio.create_task(_read_stream(proc.stdout, stdout_chunks, "stdout")))
        if proc.stderr:
            tasks.append(asyncio.create_task(_read_stream(proc.stderr, stderr_chunks, "stderr")))
        if tasks:
            await asyncio.gather(*tasks)
        await proc.wait()

    # ── No-output watchdog (polling, lightweight) ─────────────────────────────
    async def _no_output_watchdog() -> None:
        nonlocal no_output_timed_out
        if no_output_deadline is None:
            return
        while True:
            await asyncio.sleep(0.25)
            if proc.returncode is not None:
                return
            if no_output_deadline and time.monotonic() > no_output_deadline:
                no_output_timed_out = True
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass  # process already gone; expected
                return

    try:
        communicate_task = asyncio.create_task(_communicate())
        watchdog_task = asyncio.create_task(_no_output_watchdog())

        await asyncio.wait_for(communicate_task, timeout=opts.timeout_s)
        watchdog_task.cancel()

    except asyncio.TimeoutError:
        timed_out = True
        watchdog_task.cancel()
        try:
            proc.kill()
        except ProcessLookupError:
            pass  # process already gone; expected
        # Drain remaining output
        try:
            await asyncio.wait_for(asyncio.shield(communicate_task), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass  # output drain timed out or cancelled; expected

    elapsed_ms = (time.monotonic() - t0) * 1000.0

    if no_output_timed_out:
        termination = "no_output_timeout"
    elif timed_out:
        termination = "timeout"
    elif proc.returncode is not None and proc.returncode < 0:
        termination = "signal"
    else:
        termination = "exit"

    await _emit(
        opts.on_event,
        "exit" if termination == "exit" else termination,
        str(proc.returncode),
    )

    stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")

    stdout, stderr, truncated = _apply_cap(stdout, stderr, opts.output_cap)

    return ProcessResult(
        stdout=stdout,
        stderr=stderr,
        returncode=proc.returncode if not timed_out else None,
        pid=pid,
        elapsed_ms=elapsed_ms,
        termination=termination,
        truncated=truncated,
    )


# ── Sync convenience wrapper ──────────────────────────────────────────────────


def run_process_sync(
    argv: list[str],
    opts: ProcessOptions | None = None,
    *,
    extra_timeout_s: float = 5.0,
) -> ProcessResult:
    """
    Synchronous wrapper around ``run_process``.

    Safe to call from both sync and async contexts:
    - If a running event loop exists the coroutine is submitted via
      ``run_coroutine_threadsafe`` to avoid nest-asyncio issues.
    - Otherwise ``asyncio.run()`` is used.

    Args:
        argv:            Command + arguments list.
        opts:            Execution options.
        extra_timeout_s: Extra float seconds added to ``opts.timeout_s`` when
                         blocking on the future, so the outer wait never fires
                         before the subprocess timeout does.
    """
    coro = run_process(argv, opts)
    total_s = (opts.timeout_s if opts else 60.0) + extra_timeout_s
    try:
        loop = asyncio.get_running_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=total_s)
    except RuntimeError:
        return asyncio.run(coro)
