"""Windows argument expansion: the path parts Click expands, never the variables.

PowerShell and cmd.exe hand native programs their arguments unexpanded, so Click (under
Typer) expands them itself on Windows: ``~``, ``%VAR%``/``$VAR`` and glob patterns. That
is what makes ``navig x *.txt`` work on Windows — and it is also where two problems
started:

1. **It crashed.** ``glob(arg, recursive=True)`` recursed once per path segment, so a long
   JavaScript expression for ``navig cdp eval`` full of ``/`` and ``[`` raised
   ``RecursionError``, which Click does not catch: the command died before it ran and
   filed a crash report. Fixed by expanding **one argument at a time** and passing
   anything longer than any real path through untouched.
2. **It leaks.** ``os.path.expandvars`` substitutes the **local** machine's variables into
   the argument — so ``navig run "echo $HOME"`` sends *this* computer's home directory to
   a remote host, and a variable holding a credential sends that. The remote shell should
   expand its own variables: a local expansion is both wrong and a disclosure. So this
   module expands **no** environment variables.

Everything Click did that is path-shaped still happens, with Click's exact semantics
(expanduser → glob → keep the argument when nothing matched): ``~``, ``*.txt``, ``**``.
``main()`` hands Typer these arguments with ``windows_expand_args=False`` so Click does
not expand a second time.
"""

from __future__ import annotations

import os
import re
import sys
from glob import glob

# Windows' MAX_PATH is 260 and long paths top out at 32,767; nobody types a glob longer
# than this. Anything longer is content, and globbing content is how the crash happened.
MAX_PATTERN_LEN = 1024


def _expand_one(arg: str) -> list[str]:
    """One argument, Click's rules minus environment variables. Never raises."""
    expanded = os.path.expanduser(arg)
    try:
        matches = glob(expanded, recursive=True)
    except (re.error, RecursionError, OSError, ValueError, MemoryError):
        # A pattern the regex engine or the OS refuses is not an error for the user: pass
        # the argument through exactly as typed, the way Click treats a glob that matches
        # nothing.
        return [expanded]
    return matches or [expanded]


def expand_windows_args(argv: list[str]) -> list[str]:
    """Expand globs and ``~`` per argument; never expand variables, never raise."""
    out: list[str] = []
    for arg in argv:
        if len(arg) > MAX_PATTERN_LEN:
            out.append(arg)
            continue
        try:
            out.extend(_expand_one(arg))
        except Exception:  # noqa: BLE001 — expansion must never take the command down
            out.append(arg)
    return out


def cli_args() -> list[str] | None:
    """The argv to hand Typer: expanded safely on Windows, ``None`` (= sys.argv) elsewhere."""

    if sys.platform != "win32":
        return None
    return expand_windows_args(sys.argv[1:])
