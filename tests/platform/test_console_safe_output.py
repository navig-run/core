"""A dead console handle must never abort a NAVIG command.

Rich picks the *legacy* Win32 renderer on Windows and flushes through
``WriteConsoleW``. When stdout is a captured pipe — or the console it was handed has
gone away — that flush raises ``OSError: [Errno 22] Invalid argument`` from
``rich/_win32_console.py``. The exception escaped ``console.print`` and reached the
crash handler, so a command whose output was redirected became an unhandled crash plus
a ``crash-*.json`` dump. On 2026-10-22 that produced ten dumps in one morning, all from
the same frame (``navig tasks add``, ``plugin list``, ``tasks done``, …).

Output is the least important thing in the process: losing the styled payload must
never lose the command. ``console_helper.console`` is therefore a ``SafeConsole`` that
contains a failing write, drops the render buffer and (best effort) emits the text
unstyled.
"""

from __future__ import annotations

import io

from rich.console import Console

from navig.console_helper import _make_safe_console


class _ExplodingStream:
    """A stream whose writes fail the way a dead legacy console handle does."""

    def __init__(self, failures: int = 10**6) -> None:
        self.failures = failures
        self.written: list[str] = []

    def write(self, text: str) -> int:
        if self.failures > 0:
            self.failures -= 1
            raise OSError(22, "Invalid argument")
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise OSError(22, "Invalid argument")

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError(9, "Bad file descriptor")


def _safe_console(stream) -> Console:
    return _make_safe_console(Console)(file=stream, force_terminal=False)


def test_dead_console_handle_never_raises() -> None:
    stream = _ExplodingStream()
    console = _safe_console(stream)

    console.print("hello from a dead handle")  # must not raise

    assert stream.written == [], "nothing should have been written to a dead handle"


def test_transient_failure_falls_back_to_unstyled_text() -> None:
    """One failed flush must not lose the payload: the text is retried unstyled."""
    stream = _ExplodingStream(failures=1)  # the styled write fails exactly once
    console = _safe_console(stream)

    console.print("payload survives")

    emitted = "".join(stream.written)
    assert "payload survives" in emitted, (
        "the command's text was lost when the styled write failed once"
    )


def test_closed_stream_is_contained() -> None:
    stream = io.StringIO()
    stream.close()
    console = _safe_console(stream)

    console.print("into a closed stream")  # ValueError, not OSError — also contained


def test_healthy_console_still_prints() -> None:
    stream = io.StringIO()
    console = _safe_console(stream)

    console.print("normal output")

    assert "normal output" in stream.getvalue()


def _force_legacy_render(monkeypatch):
    """Make rich choose the legacy Win32 renderer, and count how often it is used.

    rich only takes that branch when ``legacy_windows`` is set AND ``get_fileno(file)``
    is a std stream, so a plain StringIO never reaches it — which made the first version
    of this test tautological. Patch the fileno probe, the term constructor and the
    renderer itself; each is imported inside the call, so module attributes win.
    """
    import rich._win32_console as rich_win32
    import rich._windows_renderer as rich_renderer
    import rich.console as rich_console

    calls = {"n": 0}

    def _boom(*args, **kwargs):
        calls["n"] += 1
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(rich_console, "get_fileno", lambda stream: 1)
    monkeypatch.setattr(rich_win32, "LegacyWindowsTerm", lambda stream: object())
    monkeypatch.setattr(rich_renderer, "legacy_windows_render", _boom)
    return calls


def test_legacy_windows_renderer_failure_is_contained(monkeypatch) -> None:
    """The exact frame from the crash dumps: legacy_windows_render raises OSError 22."""
    calls = _force_legacy_render(monkeypatch)

    stream = io.StringIO()
    console = _make_safe_console(Console)(
        file=stream, force_terminal=True, legacy_windows=True
    )

    console.print("the command must survive this")  # must not raise

    assert calls["n"] >= 1, "the legacy renderer was never exercised — this test proves nothing"
    assert "the command must survive this" in stream.getvalue(), (
        "the payload should be retried unstyled after the legacy renderer failed"
    )


def test_class_guard_covers_foreign_consoles(monkeypatch) -> None:
    """typer builds its OWN Console for --help; the class-level guard must cover it.

    ``navig run --help`` / ``navig secret --help`` kept crashing after only the
    console_helper singleton was hardened, because typer's console is not ours.
    """
    import navig.console_helper as ch

    calls = _force_legacy_render(monkeypatch)
    ch.harden_console_writes()  # what main() does before any command prints

    stream = io.StringIO()
    foreign = Console(file=stream, force_terminal=True, legacy_windows=True)
    assert not isinstance(foreign, ch._SafeConsole), "this must be a PLAIN rich Console"

    foreign.print("typer's help output")  # must not raise

    assert calls["n"] >= 1, "the plain Console was not covered by the class-level guard"


def _raise_at(filename: str, named: bool = False) -> OSError:
    """Raise inside a frame whose filename we choose, and return the OSError."""
    source = (
        "raise OSError(22, 'Invalid argument', 'C:/x/config.yaml')"
        if named
        else "raise OSError(22, 'Invalid argument')"
    )
    try:
        exec(compile(source, filename, "exec"), {})  # noqa: S102 — deliberate probe
    except OSError as exc:
        return exc
    raise AssertionError("probe did not raise")


def test_dead_output_detection_matches_click_echo_site() -> None:
    """`navig run --help` dies inside click.utils.echo — that raise site must count."""
    from navig.main import _is_dead_output_error

    exc = _raise_at("C:/venv/Lib/site-packages/click/utils.py")

    assert _is_dead_output_error(exc) is True, (
        "click's echo raise site was not recognised — `navig run --help` would file a "
        "crash report again"
    )


def test_dead_output_detection_ignores_someone_elses_oserror() -> None:
    """The same errno raised by command code keeps its crash report."""
    from navig.main import _is_dead_output_error

    exc = _raise_at("C:/labs/core/navig/commands/run.py")

    assert _is_dead_output_error(exc) is False, (
        "a real OSError raised by command code was classified as a dead console and "
        "swallowed without a crash report"
    )


def test_stream_guard_absorbs_a_dead_handle() -> None:
    """CPython flushes sys.stdout at shutdown — a dead pipe must not turn exit 0 into 120."""

    from navig.main import _DeadOutputTolerantStream

    class _Dead(io.StringIO):
        def write(self, text):  # noqa: ANN001, ANN201
            raise OSError(22, "Invalid argument")

        def flush(self) -> None:
            raise OSError(22, "Invalid argument")

    wrapped = _DeadOutputTolerantStream(_Dead())

    wrapped.write("payload")
    wrapped.flush()
    wrapped.close()


def test_dead_output_detection_rejects_real_command_io_errors() -> None:
    """A real OSError from command code must keep its crash report.

    Every command's traceback contains click/typer frames, so a classifier matching ANY
    frame swallowed a dead-child-pipe EPIPE, a closed fd, or a device EINVAL from a
    plugin — the crash silently became "exit 1, no dump". Only the innermost raise site
    may be an output helper.
    """
    import click

    from navig.main import _is_dead_output_error

    @click.command()
    def _cmd() -> None:
        raise OSError(32, "Broken pipe")  # dead child stdin — command I/O, not output

    # click's own main() handles EPIPE specially, so call the command body directly:
    # what matters is that the raise site is the command, not an output helper.
    try:
        _cmd.callback()
    except OSError as exc:
        assert _is_dead_output_error(exc) is False, (
            "a real EPIPE raised by command code was classified as a dead console"
        )
    else:  # pragma: no cover
        raise AssertionError("the command did not raise")


def test_dead_output_detection_requires_an_output_raise_site() -> None:
    """The filename guard is load-bearing: an output site with a named file is real I/O."""
    from navig.main import _is_dead_output_error

    rich_console = "C:/venv/Lib/site-packages/rich/console.py"

    assert _is_dead_output_error(_raise_at(rich_console)) is True, (
        "a console write from rich's raise site was not recognised"
    )
    assert _is_dead_output_error(_raise_at(rich_console, named=True)) is False, (
        "a NAMED file failing at rich's raise site is real I/O — it must keep its dump"
    )


def test_main_installs_the_stream_guard(monkeypatch) -> None:
    """main() must actually install the wrapper — no-op'ing the wiring brings exit 120 back."""
    import sys as _sys

    from navig import main as navig_main

    monkeypatch.setattr(_sys, "stdout", io.StringIO())
    navig_main._install_dead_output_guard()

    assert isinstance(_sys.stdout, navig_main._DeadOutputTolerantStream), (
        "main() did not wrap sys.stdout; a dead pipe would re-raise at shutdown"
    )


def test_the_global_console_uses_the_safe_subclass() -> None:
    """The CLI prints through ``console_helper.console`` — it must be the safe one."""
    import navig.console_helper as ch

    ch._ensure_rich()

    assert isinstance(ch.console._load(), ch._SafeConsole), (
        "console_helper.console still builds a plain rich Console; a failing handle can "
        "abort every command again"
    )
