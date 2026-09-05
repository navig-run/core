"""Every local-command executor must read BOTH a UTF-8 tool and a console tool.

navig has three of them and they are reached by different commands:

    commands/remote.py::_execute_local_command   <- `navig run` on a local host
    remote.py::RemoteOperations.execute_local    <- the local-host bypass inside
                                                    execute_command (deploy, discovery, ai)
    core/connection.py::LocalConnection.run      <- LocalOperations, i.e. `navig local *`

They are separate implementations of one behaviour, so a fix applied to one proves nothing
about the others — which is exactly how this defect survived a first pass that corrected
only the third.

Why both directions matter. The shell does NOT normalise what its child wrote; measured on
this machine, cmd.exe and `powershell -Command` both pass the bytes straight through::

    git log -1 --format=%s   ->  b'... main \\xe2\\x80\\x94 and ...'    (UTF-8)
    whoami /groups           ->  b'BUILTIN\\\\\\x80\\xa4\\xac...'         (cp866)

So a fixed codec is wrong for one of them whichever is chosen, and a test that checks only
one direction will happily pass on a half-fix:

    utf-8 only          git ok    console MANGLED  (U+FFFD)
    console page only   git MANGLED  console ok
    utf-8 -> console    both ok
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="the ANSI/console/UTF-8 three-way split is a Windows concept",
)

# BUILTIN\Administrators, whose display name is localized (non-ASCII) on most non-English
# installs. Translated by .NET rather than shelling out to `whoami`, because PowerShell
# resolves that name through PATH and can find a Git Bash /usr/bin/whoami — a property of
# the developer's shell, not of navig.
_SID_NAME = (
    "(New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544'))"
    ".Translate([System.Security.Principal.NTAccount]).Value"
)
_PS_SID = f'powershell -NoProfile -Command "{_SID_NAME}"'

# A commit whose subject carries a UTF-8 em dash. Read through git, which writes UTF-8
# regardless of the console code page.
_GIT_SUBJECT = "git log -1 --format=%s 4d1890135"
_EM_DASH = "—"


def _console_output_is_localized() -> bool:
    raw = subprocess.run(
        ["powershell", "-NoProfile", "-Command", _SID_NAME], capture_output=True, timeout=60
    ).stdout
    return any(b > 127 for b in raw)


def _git_subject_has_non_ascii() -> bool:
    raw = subprocess.run(
        ["git", "log", "-1", "--format=%s", "4d1890135"], capture_output=True, timeout=60
    ).stdout
    return _EM_DASH.encode() in raw


def _assert_console_tool_readable(text: str, who: str) -> None:
    assert any(ord(c) > 127 for c in text), f"{who}: the localized name came back ASCII-only"
    assert "�" not in text, (
        f"{who}: replacement characters — console output decoded as UTF-8 or the ANSI page"
    )
    # cp1251-over-cp866 mojibake is the historical signature: it produces currency/soft-hyphen
    # symbols where letters belong, so require the result to be letters.
    letters = [c for c in text.split("\\")[-1] if ord(c) > 127]
    assert letters and all(c.isalpha() for c in letters), (
        f"{who}: non-letter characters inside the group name -> wrong code page: {text!r}"
    )


def _assert_utf8_tool_readable(text: str, who: str) -> None:
    assert _EM_DASH in text, (
        f"{who}: git's UTF-8 em dash did not survive — a fixed console code page mangles it "
        f"into two Cyrillic characters. Got: {text!r}"
    )


# --------------------------------------------------------------------------------------
# `navig run` on a local host
# --------------------------------------------------------------------------------------


def test_navig_run_local_reads_a_console_tool() -> None:
    from navig.commands.remote import _execute_local_command

    if not _console_output_is_localized():
        pytest.skip("this Windows install reports the group name in ASCII")
    result = _execute_local_command(_PS_SID)
    assert result.returncode == 0, result.stderr
    _assert_console_tool_readable(result.stdout.strip(), "_execute_local_command")


def test_navig_run_local_reads_a_utf8_tool() -> None:
    from navig.commands.remote import _execute_local_command

    if not _git_subject_has_non_ascii():
        pytest.skip("that commit is not reachable in this checkout")
    result = _execute_local_command(_GIT_SUBJECT)
    assert result.returncode == 0, result.stderr
    _assert_utf8_tool_readable(result.stdout, "_execute_local_command")


# --------------------------------------------------------------------------------------
# the local-host bypass inside RemoteOperations.execute_command
# --------------------------------------------------------------------------------------


def test_execute_local_reads_a_console_tool() -> None:
    from navig.remote import RemoteOperations

    if not _console_output_is_localized():
        pytest.skip("this Windows install reports the group name in ASCII")
    result = RemoteOperations(None).execute_local(_PS_SID)
    assert result.returncode == 0, result.stderr
    _assert_console_tool_readable(result.stdout.strip(), "execute_local")


def test_execute_local_reads_a_utf8_tool() -> None:
    from navig.remote import RemoteOperations

    if not _git_subject_has_non_ascii():
        pytest.skip("that commit is not reachable in this checkout")
    result = RemoteOperations(None).execute_local(_GIT_SUBJECT)
    assert result.returncode == 0, result.stderr
    _assert_utf8_tool_readable(result.stdout, "execute_local")


# --------------------------------------------------------------------------------------
# LocalConnection.run — `navig local *`
# --------------------------------------------------------------------------------------


def test_local_connection_reads_a_console_tool() -> None:
    from navig.core.connection import LocalConnection

    if not _console_output_is_localized():
        pytest.skip("this Windows install reports the group name in ASCII")
    result = LocalConnection(os_type="windows").run(_SID_NAME, timeout=60.0)
    assert result.exit_code == 0, result.stderr
    _assert_console_tool_readable(result.stdout.strip(), "LocalConnection.run")


def test_local_connection_reads_a_utf8_tool() -> None:
    """The direction the first pass at this broke: a fixed console page mangles git."""
    from navig.core.connection import LocalConnection

    if not _git_subject_has_non_ascii():
        pytest.skip("that commit is not reachable in this checkout")
    result = LocalConnection(os_type="windows").run(_GIT_SUBJECT, timeout=60.0)
    assert result.exit_code == 0, result.stderr
    _assert_utf8_tool_readable(result.stdout, "LocalConnection.run")


# --------------------------------------------------------------------------------------
# shared invariants
# --------------------------------------------------------------------------------------


def test_all_three_executors_agree() -> None:
    """They implement one behaviour; a divergence means one of them was missed again."""
    from navig.commands.remote import _execute_local_command
    from navig.core.connection import LocalConnection
    from navig.remote import RemoteOperations

    if not _console_output_is_localized():
        pytest.skip("this Windows install reports the group name in ASCII")

    a = _execute_local_command(_PS_SID).stdout.strip()
    b = RemoteOperations(None).execute_local(_PS_SID).stdout.strip()
    c = LocalConnection(os_type="windows").run(_SID_NAME, timeout=60.0).stdout.strip()
    assert a == b == c, f"executors disagree:\n  navig run: {a!r}\n  bypass: {b!r}\n  local: {c!r}"


def test_streams_are_str_not_bytes() -> None:
    """Backward compatibility: callers index, .strip() and json-serialise these."""
    from navig.commands.remote import _execute_local_command
    from navig.remote import RemoteOperations

    for result in (
        _execute_local_command("echo navig-ok"),
        RemoteOperations(None).execute_local("echo navig-ok"),
    ):
        assert isinstance(result.stdout, str)
        assert isinstance(result.stderr, str)
        assert "navig-ok" in result.stdout


def test_uncaptured_runs_are_untouched() -> None:
    """capture_output=False streams to the terminal; there is nothing to decode."""
    from navig.commands.remote import _execute_local_command

    result = _execute_local_command("echo navig-ok", capture_output=False)
    assert result.returncode == 0


def test_output_matches_a_direct_read_of_the_same_bytes() -> None:
    """Independent oracle: decode the same bytes by hand and compare.

    An assertion on kwargs would pass just as happily with the wrong codec named; this
    reproduces the decision from the raw bytes instead.
    """
    from navig.core.connection import LocalConnection
    from navig.core.proc_text import decode_console_output

    if not _console_output_is_localized():
        pytest.skip("this Windows install reports the group name in ASCII")

    raw = subprocess.run(
        ["powershell", "-NoProfile", "-Command", _SID_NAME], capture_output=True, timeout=60
    ).stdout
    expected = decode_console_output(raw).strip()

    result = LocalConnection(os_type="windows").run(_SID_NAME, timeout=60.0)
    assert result.stdout.strip() == expected
