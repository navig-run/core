"""Guard: a UTF-8 BOM must never sit in front of a `#!` shebang.

The bug this exists for
-----------------------
`plugins/navig-mini/install.sh` began with `EF BB BF #!/bin/sh`. That file is the target
of navig-mini's two documented install commands:

    curl -fsSL https://get.navig.run/mini | sh
    python3 <(wget -qO- https://get.navig.run/mini.py)

A BOM in front of the shebang breaks both of the ways such a file is run, and it breaks
them differently:

* **Piped to a shell** the shebang is not honoured anyway, but the BOM makes byte 0 stop
  being `#`, so line 1 is no longer a comment -- it is parsed as a COMMAND. Measured:
  `sh: line 1: <BOM>#!/bin/sh: No such file or directory`. The script then continues, so
  it "works" while the first thing a new user ever sees is an error naming a file that
  does exist. Nothing fails, so nothing gets reported.
* **Saved and executed** (`chmod +x install.sh; ./install.sh`) the kernel reads the first
  two bytes looking for `#!`, finds `EF BB`, and refuses -- an outright failure.

Both are invisible to every other check in this repo: the file is valid UTF-8, `sh -n`
parses it (the bad line is a command, not a syntax error), and ruff/shellcheck never see
it. Only running it shows the problem, and the piped form does not even fail.

Scope, and the one legitimate exemption
---------------------------------------
The class is "a BOM before a shebang", not "a BOM in a `.sh` file" -- the same breakage
applies to an extensionless script or a directly-executed `.py`. Measured over 22,427
files: 480 carry a shebang and, after the fix, ZERO of the non-`.ps1` ones carry a BOM.

`.ps1` is exempt, and the exemption is real rather than a suppression: Windows PowerShell
5.1 reads a BOM-less file as the ANSI code page, so a BOM is what keeps non-ASCII text
intact there (see the recorded installer-encoding incident). PowerShell does not honour a
shebang on Windows, so the failure mode above cannot occur. Five `.ps1` files in the tree
carry a BOM and also open with a `#!` line for POSIX `pwsh` users.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# Matched against the REPO-RELATIVE path. Matching absolute paths instead skips the repo
# itself whenever the checkout lives under one of these names -- which is every
# `.dev/worktrees/<slug>` worktree, the sanctioned parallel-work path here -- and a guard
# that scanned nothing is indistinguishable from a guard that passed.
_SKIP_DIRS = {
    ".git",
    ".lab",
    ".dev",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
    "out",
    ".next",
    ".venv",
    "venv",
    "target",
    "site-packages",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
}

_BOM = b"\xef\xbb\xbf"

# A shebang-bearing file whose BOM is correct. PowerShell needs it; Windows ignores the
# shebang. Anything added here must state why the BOM is required, not merely tolerated.
_EXEMPT_SUFFIXES = {".ps1"}

# A broken scan returns an empty finding list, which looks exactly like a clean run. The
# floor is well under the 480 measured so ordinary churn cannot trip it, and far above the
# zero a mis-rooted walk would produce.
_MIN_SHEBANG_FILES = 200


@lru_cache(maxsize=1)
def _scan() -> tuple[tuple[str, ...], int, int]:
    """(offenders, shebang-bearing files seen, exempt files carrying a BOM).

    One walk, cached: this guard runs on every change via `sourceGuardArgs`, and walking
    22k files once per assertion is the difference between a 4s step and an 11s one.
    """
    offenders: list[str] = []
    shebangs = 0
    exempt_with_bom = 0
    for root, dirs, files in os.walk(REPO):
        rel_root = Path(root).relative_to(REPO)
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        if _SKIP_DIRS & set(rel_root.parts):
            continue
        for name in files:
            path = Path(root) / name
            try:
                with path.open("rb") as fh:
                    head = fh.read(8)
            except OSError:
                continue
            has_bom = head.startswith(_BOM)
            body = head[len(_BOM) :] if has_bom else head
            if not body.startswith(b"#!"):
                continue
            shebangs += 1
            if not has_bom:
                continue
            if path.suffix.lower() in _EXEMPT_SUFFIXES:
                exempt_with_bom += 1
            else:
                offenders.append((path.relative_to(REPO)).as_posix())
    return tuple(sorted(offenders)), shebangs, exempt_with_bom


def test_no_bom_before_a_shebang() -> None:
    offenders, shebangs, _ = _scan()
    assert shebangs >= _MIN_SHEBANG_FILES, (
        f"only {shebangs} shebang-bearing files found (expected >= {_MIN_SHEBANG_FILES}) -- "
        f"the walk is mis-rooted at {REPO}, so this guard read almost nothing. A scan that "
        f"finds no files reports no offenders, which is not the same as there being none."
    )
    assert not offenders, (
        "a UTF-8 BOM sits in front of the shebang in:\n"
        + "\n".join(f"    {o}" for o in offenders)
        + "\n\nPiped to a shell this makes line 1 a COMMAND instead of a comment "
        "(`sh: line 1: <BOM>#!/bin/sh: No such file or directory`), and executed directly "
        "the kernel does not recognise the shebang at all. Strip the three leading bytes; "
        "change nothing else.\n"
        "If the file is PowerShell, give it a .ps1 suffix -- there the BOM is required."
    )


def test_the_exemption_covers_real_files_and_not_the_whole_tree() -> None:
    """The .ps1 carve-out must be load-bearing, not a hole big enough to hide the class.

    If no exempt file actually carries a BOM before a shebang, the exemption is dead and
    should go. If it covered most shebang files, the guard would be watching nothing.
    """
    _, shebangs, exempt_with_bom = _scan()
    assert exempt_with_bom > 0, (
        "no exempt file carries a BOM before a shebang any more -- the .ps1 carve-out is "
        "now dead code and should be removed rather than left as a standing hole."
    )
    assert exempt_with_bom < shebangs // 2, (
        f"the exemption covers {exempt_with_bom} of {shebangs} shebang files -- that is no "
        f"longer a carve-out, it is most of the surface this guard is supposed to watch."
    )
