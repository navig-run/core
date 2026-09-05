"""Test output must not be re-flowed by the terminal width.

Rich hard-wraps at the console width and assumes **80** when stdout is not a tty.
That inserts a newline mid-sentence, so `assert "nothing recorded yet" in output`
fails on output that is exactly right — and it fails *intermittently*, because the
wrap column moves with the message length. These views print PATHS, and under
`-n auto` xdist lengthens tmp_path with the worker id, so the same assert passes
solo, passes for its directory, and fails only in the full run.

`tests/conftest.py` pins COLUMNS so that variable is simply gone. These tests are
the proof it is actually in force: without them the suite would pass whether the
setting worked or not, since the wrap only bites on long lines.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]

# Comfortably wider than 80 and than the 120 a real PowerShell reports, so a failure
# means the width is not being applied rather than "that line was unusually long".
_MIN_WIDTH = 160

_PHRASE = "nothing recorded yet"

# A realistic message: these views print a path, and an xdist tmp_path is this long.
_LONG_MESSAGE = (
    "no ledger at "
    "E:/projects/apps/navig/.dev/worktrees/truth/core/.dev/tmp/"
    "pytest-of-subdose/pytest-9/popen-gw6/test_missing_ledger0/nope.jsonl"
    f" \u2014 {_PHRASE}"
)

# The message is passed as ARGV, never interpolated into the generated source: a
# path pasted raw into source turns \n \t \a into real control characters, which
# looks exactly like a wrap and cost me one debugging cycle here.
_SCRIPT = "import sys\nfrom navig import console_helper as ch\nch.console.print(sys.argv[1])\n"


def _render(message: str, columns: str | None = None) -> str:
    """Render `message` in a child process. `columns=None` INHERITS the session's
    width (what a CliRunner-driven test gets); a value overrides it."""
    env = {**os.environ, "PYTHONPATH": str(CORE)}
    if columns is not None:
        env["COLUMNS"] = columns
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT, message],
        capture_output=True,
        text=True,
        cwd=CORE,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_columns_is_set_wide_for_the_session() -> None:
    value = os.environ.get("COLUMNS")
    assert value is not None, (
        "COLUMNS is unset, so Rich falls back to 80 when stdout is piped and will "
        "re-flow prose mid-sentence. tests/conftest.py is supposed to pin it."
    )
    assert int(value) >= _MIN_WIDTH, (
        f"COLUMNS={value} is narrow enough to wrap ordinary CLI messages; "
        f"expected >= {_MIN_WIDTH}. See tests/conftest.py."
    )


def test_a_long_message_survives_the_console_intact() -> None:
    """The behaviour, not just the env var — and in a CHILD process, which is what a
    CliRunner-driven test effectively measures: a fresh Console reading the
    inherited environment."""
    out = _render(_LONG_MESSAGE, columns=None)  # inherit the session's COLUMNS

    assert _PHRASE in out, (
        "the console split a phrase across lines — COLUMNS is not reaching the "
        f"child process, so every substring assert on CLI output is a coin flip.\n"
        f"got:\n{out}"
    )
    assert len(out.strip().splitlines()) == 1, (
        f"expected one line, got {len(out.strip().splitlines())} — re-flowed:\n{out}"
    )


def test_the_check_would_notice_a_narrow_console() -> None:
    """Anti-vacuity: prove the assertion above CAN fail. The same message at
    COLUMNS=80 must wrap — otherwise the test measures nothing and would stay green
    if the conftest setting were deleted."""
    out = _render(_LONG_MESSAGE, columns="80")
    assert len(out.strip().splitlines()) > 1, (
        "at COLUMNS=80 this message did NOT wrap, so the wide-console test above "
        "proves nothing. Either Rich stopped honouring COLUMNS or the message is no "
        f"longer long enough — pick a longer one.\ngot:\n{out}"
    )
