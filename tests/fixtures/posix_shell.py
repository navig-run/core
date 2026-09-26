"""The one place a test resolves a POSIX shell — on Windows, Git Bash and never the WSL launcher.

Why this exists (measured 2026-09-19): three test files each resolved ``bash`` their own
way, and two of the three were wrong on this machine.

* ``CreateProcess`` searches ``System32`` BEFORE ``PATH``. Spawning a bare ``["bash", …]``
  on Windows therefore runs ``C:\\Windows\\System32\\bash.exe`` — the WSL launcher — even
  when ``shutil.which("bash")`` (which searches PATH only) reports Git Bash. The launcher
  cannot take an ``E:\\…`` script path (``/bin/bash: E:projectsapps…: No such file``) and
  translates every argument through WSL, so a ``-c`` script naming Windows temp paths runs
  against paths that do not exist there — or, for a restore script, deletes under the
  wrong root.
* Text-mode stdin (``text=True``) writes ``\\n`` as ``os.linesep``, so a script fed on stdin
  gains a CR after every line-continuation backslash and bash reports a syntax error the
  LF file never has. Feed scripts as BYTES or by path.
* ``C:\\Program Files\\Git\\bin`` is one of several places Git Bash lives (scoop puts it
  under ``~/scoop/apps/git/<ver>/bin``); derive it from ``git`` itself.

Usage::

    from tests.fixtures.posix_shell import POSIX_SHELL, needs_posix_shell

    @needs_posix_shell
    def test_x():
        subprocess.run([POSIX_SHELL, "-c", script], …)      # full path, never bare "bash"
"""

from __future__ import annotations

import pytest


def find_posix_shell() -> str | None:
    """Absolute path of a bash (or sh) that can run a script by path on this host, or None.

    Delegates to the product's own resolver so the tests and the trigger runner cannot
    disagree about which bash a Windows box has.
    """
    from navig.platform.process import posix_shell

    return posix_shell()


POSIX_SHELL = find_posix_shell()
needs_posix_shell = pytest.mark.skipif(
    POSIX_SHELL is None, reason="no POSIX shell that can run a script by path (Git Bash on Windows)"
)
