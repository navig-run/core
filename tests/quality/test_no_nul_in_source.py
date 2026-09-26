"""Guard: a tracked text source file must never contain a NUL byte.

The bug this exists for
-----------------------
`scripts/test/tauri-bundle-guard.test.mjs` was written with a real `0x00` byte where
the two-character JS escape `\0` was meant. JavaScript parses both to the same string,
so every test passed -- and **git classifies any file containing a NUL as binary**. Its
diff read `Bin 5145 -> 8811 bytes` and showed nothing.

That is how it survived review: the diff of the commit that introduced it was
unreadable, in the file whose entire job is to be reviewable. Nothing else in this repo
can see it -- ruff, tsc, eslint and every test are happy, because the *program* is
correct. Only the tooling around the file breaks.

The second shape, which is worse
--------------------------------
The same check catches a file saved in **UTF-16**, because UTF-16-encoded ASCII carries
a NUL after every character. Measured when this guard was written:
`core/navig/builtin/tools/app.tool.json` was UTF-16LE-with-BOM *and* had one stray
trailing byte, so it decoded as neither UTF-8 nor UTF-16 -- it could not be parsed as
JSON in any encoding at all. Its 57 sibling `*.tool.json` files all parse as UTF-8, so
this was an accident rather than a convention, and it shipped in the wheel that way.

A file that no encoding can read is invisible to a linter (never parsed), to a type
checker (not code) and to a test (nothing imports it). The NUL bytes are the only signal
it emits.

Scope
-----
Text-source extensions only, over the same tree the sibling byte-hygiene guard walks. A
NUL in a genuine binary is data; a NUL in a `.ts` file is a mistake or an encoding
accident. Deliberate NUL *values* stay expressible -- write the escape (`"\\0other"`,
`` `${a}\\0${b}` ``), which produces the identical string at runtime and leaves the
source readable. Both live cases in this tree were exactly that and were converted.

There is no exemption list. An entry here would assert that a file git cannot diff is
acceptable, which is the thing being prevented.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# Built with explicit code points, never an escape sequence: a source file that
# writes "\0" one layer too shallow gets a real NUL and trips this very guard.
# That happened three times while this was being written.
_NUL = bytes([0])
_NUL_TEXT = chr(0)

# Extensions whose contents are source a human reads and git diffs. Deliberately a
# whitelist: the point is not "no NULs anywhere" (a .png is full of them) but "no
# NULs in something that is supposed to be text".
_TEXT_SUFFIXES = {
    ".py", ".pyi", ".rs", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".md", ".txt", ".rst", ".css", ".scss", ".html", ".svg", ".xml",
    ".sh", ".bash", ".ps1", ".bat", ".cmd", ".sql", ".env", ".lock",
}

# Measured at 12,519 text files. The floor sits far below that so ordinary churn cannot
# trip it, and far above the zero a mis-rooted walk would produce.
_MIN_TEXT_FILES = 5_000


@lru_cache(maxsize=1)
def _scan() -> tuple[tuple[tuple[str, int], ...], int]:
    """((offender, nul-count), ...), text files seen.

    Scoped to TRACKED files. An `os.walk` here reads whatever happens to be on the
    machine -- a gitignored `.backup/` holding vendored site-packages, a stray
    `desktop.ini`, a developer's `pytest_output.txt` -- and fails for reasons that have
    nothing to do with the commit. Measured: the walk reported 5 offenders, all
    untracked; `git ls-files` reported 3, all real. What ships is what is tracked.

    One pass, cached: this runs on every change via `sourceGuardArgs`.
    """
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO,
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        # Returning an empty scan would look exactly like a clean run; the floor below
        # turns that into a loud failure instead.
        return (), 0

    offenders: list[tuple[str, int]] = []
    seen = 0
    for rel in listing.decode("utf-8", "replace").split(_NUL_TEXT):
        if not rel:
            continue
        path = REPO / rel
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        seen += 1
        count = 0
        try:
            with path.open("rb") as fh:
                while chunk := fh.read(1 << 20):
                    count += chunk.count(_NUL)
                    if count:
                        break  # one is enough to fail; do not read the rest
        except OSError:
            continue  # tracked but not present (a sparse checkout)
        if count:
            offenders.append((rel, count))
    return tuple(sorted(offenders)), seen


def test_the_scan_actually_reads_the_tree() -> None:
    """Stated as a presence: a scan that found nothing reports no offenders."""
    _, seen = _scan()
    assert seen >= _MIN_TEXT_FILES, (
        f"only {seen} text files found (expected >= {_MIN_TEXT_FILES}) -- the walk is "
        f"mis-rooted at {REPO}, so this guard read almost nothing."
    )
    assert (REPO / "core" / "navig").is_dir() and (REPO / "plugins").is_dir(), (
        f"{REPO} does not look like the repo root; every count below it is meaningless"
    )


def test_no_nul_bytes_in_text_source() -> None:
    offenders, _ = _scan()
    assert not offenders, (
        "these text source files contain NUL bytes:\n"
        + "\n".join(f"    {path}  ({n} NUL byte{'s' if n > 1 else ''})" for path, n in offenders)
        + "\n\ngit classifies a file containing a NUL as BINARY, so its diff renders as "
        "`Bin <n> -> <m> bytes` and shows nothing -- the change cannot be reviewed, and "
        "nothing else in this repo can see the problem because the program itself is "
        "valid.\n\n"
        "If the NUL is deliberate, write the escape instead (PLACEHOLDER_A, "
        "`` `${a}\\0${b}` ``, `\"\u0000\"` in JSON): identical value at runtime, readable "
        "source.\n"
        "If the file is UTF-16, it is an encoding accident -- re-save it as UTF-8. Such a "
        "file may not parse in ANY encoding, and no linter or test will tell you."
    )
