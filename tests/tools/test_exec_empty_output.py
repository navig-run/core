"""A command that prints nothing must SAY it printed nothing.

2026-10-16: ``curl -s https://rdap.org/domain/schema.org`` exited 0 with an empty
body (rdap.org redirects; ``-s`` without ``-L`` does not follow). The agent got an
empty string back, read it as "no answer", and told the operator RDAP was
unreachable from this machine. Both shell paths now return an explicit note.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from navig.tools.bash_exec import BashExecTool, empty_output_note
from navig.tools.domains.exec_pack import _run_shell

PY = sys.executable


def _silent(code: int) -> str:
    return f"import sys; sys.exit({code})"


@pytest.mark.skipif(" " in PY, reason="BashExecTool splits on spaces; the interpreter path has one")
def test_bash_exec_names_a_silent_success():
    r = asyncio.run(BashExecTool().run({"command": f"{PY} -c pass"}))
    assert r.success
    assert r.output == empty_output_note(0)
    assert "not evidence of a network" in r.output


@pytest.mark.skipif(" " in PY, reason="BashExecTool splits on spaces; the interpreter path has one")
def test_bash_exec_names_a_silent_failure():
    r = asyncio.run(BashExecTool().run({"command": f'{PY} -c "{_silent(3)}"'}))
    assert not r.success
    assert r.output == empty_output_note(3)


@pytest.mark.skipif(" " in PY, reason="BashExecTool splits on spaces; the interpreter path has one")
def test_bash_exec_leaves_real_output_alone():
    r = asyncio.run(BashExecTool().run({"command": f'{PY} -c "print(42)"'}))
    assert r.success and r.output == "42"


@pytest.mark.parametrize(
    "command, argv",
    [
        ('python -c "print(42)"', ["python", "-c", "print(42)"]),
        ("curl -H 'Accept: x' https://a.b", ["curl", "-H", "Accept: x", "https://a.b"]),
        (r'"C:\Program Files\x.exe" -n', [r"C:\Program Files\x.exe", "-n"]),
        ("whois -nobanner schema.org", ["whois", "-nobanner", "schema.org"]),
    ],
)
def test_quoted_arguments_reach_the_program_without_their_quotes(command, argv):
    from navig.tools.bash_exec import split_command

    assert split_command(command) == argv


def test_exec_pack_adds_a_note_only_when_nothing_was_printed():
    silent = asyncio.run(_run_shell(f'"{PY}" -c pass', timeout_seconds=30))
    assert silent["returncode"] == 0 and silent["note"] == empty_output_note(0)

    spoke = asyncio.run(_run_shell(f'"{PY}" -c "print(42)"', timeout_seconds=30))
    assert "note" not in spoke and spoke["stdout"].strip() == "42"
