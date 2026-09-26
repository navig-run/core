r"""Guard: a text source file must not contain a stray C0 control character.

The bug this exists for
-----------------------
Four tracked files carried control bytes where an ESCAPE SEQUENCE was meant. Something
processed the text one layer too eagerly and turned the backslash form into the byte:

* ``services/api/tests/stranded-reconcile.test.ts`` -- a regex word boundary became a
  literal backspace, so ``/^(PRIMARY|FOREIGN|UNIQUE|CHECK|CONSTRAINT)\b/`` became
  ``/^(...)<0x08>/``. It can never match, so the ``continue`` that skips SQL constraint
  lines was dead and ``PRIMARY``/``FOREIGN`` were collected as if they were column names.
* ``scripts/test/os-harness-shot-leakcheck.test.mjs`` -- the same, twice:
  ``/\bCAN\b|NOT proof/`` became ``/<0x08>CAN<0x08>|NOT proof/``, so the first
  alternative is unmatchable and the assertion silently rests on the second alone.
* ``scripts/ci-local.mjs`` -- a Windows path in a comment, where ``\a`` became BEL and
  ``\n`` became real newlines: ``E:\projects<0x07>pps`` / ``avig\core`` split across
  three lines and unreadable.
* ``.navig/wiki/technical/architect_agent.md`` -- nine of them in one note, so the
  TypeScript invariants it records read ``<TAB>itle=`` and ``<0x07>ria-label=`` instead
  of ``title=`` and ``aria-label=``.

Why nothing else sees it
------------------------
Every one of those files is valid UTF-8 and parses fine -- the byte sits INSIDE a string
or a comment, so no linter, type checker or test objects. The two regex cases are worse
than cosmetic: the pattern still compiles, still runs, and quietly matches nothing.
Terminals make it harder still, because they RENDER a backspace by erasing the preceding
character, so the broken regex looks correct in a diff.

Scope
-----
C0 except tab, LF and CR, plus DEL. Tab is excluded deliberately: it is legitimate in
plenty of files, so flagging it would be noise -- the mangled ``\t`` cases above were
found because those files also carried a BEL. Measured over 10,919 tracked text files:
six offenders, four of them real. The ratio is why this is a gate rather than a report.
"""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

_TEXT_SUFFIXES = {
    ".py", ".pyi", ".rs", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".md", ".txt", ".rst", ".css", ".scss", ".html", ".sh", ".bash",
    ".ps1", ".bat", ".cmd", ".sql", ".lock",
}

# Never legitimate in text: every C0 except tab/LF/CR, plus DEL.
_BANNED = ({bytes([c]) for c in range(32)} - {b"\t", b"\n", b"\r"}) | {bytes([127])}

# Two files where the byte IS the point. Each entry states why, because an allowlist
# without a reason is where a real defect eventually hides.
_ALLOWED: dict[str, str] = {
    # `l.sequence === "<ETX>"` -- ETX is literally the byte a terminal sends for Ctrl+C,
    # so comparing a keypress sequence against it is the correct thing to write.
    "scripts/debugCommand/copilotDebugCommand.js": "ETX is what Ctrl+C sends",
    # Click's documented no-rewrap marker: a lone \b in a command's help text tells Click
    # not to reflow the paragraph that follows. It has to be the literal byte.
    "plugins/navig-contacts/navig_contacts/commands/contacts.py": "Click \\b no-rewrap marker",
}

# Tab is NOT in _BANNED -- it is legitimate in plenty of files. But a `\t` typed into PROSE
# and interpreted has a signature a real tab never does: it sits right after a space or an
# opening bracket and right before a lowercase letter, i.e. mid-sentence, where it ate the
# first letter of a word (`in \tests/` -> `in <TAB>ests/`). A tab that separates table
# cells follows a word character; a tab that indents starts the line. Measured over 1,888
# tracked prose files: the loose form (any mid-line tab before a lowercase letter) hit 3,
# one of them a tab-separated table; this form hit 2, both real, 0 false positives.
_PROSE_SUFFIXES = {".md", ".txt", ".rst"}
_MANGLED_TAB = re.compile(rb"[ (\[{]\t[a-z]")

# Measured at 10,919 tracked text files. Far below that so churn cannot trip it, far above
# the zero a mis-rooted or git-less scan would produce.
_MIN_TEXT_FILES = 5_000


@lru_cache(maxsize=1)
def _scan() -> tuple[tuple[tuple[str, str], ...], int]:
    """((path, "0x07 x2"), ...), text files seen. Tracked files only."""
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True, timeout=60
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return (), 0  # the floor below turns this into a loud failure, not a clean run

    offenders: list[tuple[str, str]] = []
    seen = 0
    for rel in listing.decode("utf-8", "replace").split("\0"):
        if not rel:
            continue
        path = REPO / rel
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        seen += 1
        try:
            blob = path.read_bytes()
        except OSError:
            continue  # tracked but absent (a sparse checkout)
        if rel in _ALLOWED:
            continue
        found = {c: blob.count(c) for c in _BANNED if c in blob}
        parts = [f"0x{c[0]:02x} x{n}" for c, n in sorted(found.items())]
        if path.suffix.lower() in _PROSE_SUFFIXES:
            mangled = len(_MANGLED_TAB.findall(blob))
            if mangled:
                parts.append(f"mid-sentence tab x{mangled} (a `\\t` typed as prose)")
        if parts:
            offenders.append((rel, ", ".join(parts)))
    return tuple(sorted(offenders)), seen


def test_the_scan_actually_reads_the_tree() -> None:
    """Stated as a presence: a scan that found nothing reports no offenders."""
    _, seen = _scan()
    assert (REPO / "core" / "navig").is_dir() and (REPO / "plugins").is_dir(), (
        f"{REPO} does not look like the repo root; every count below it is meaningless"
    )
    assert seen >= _MIN_TEXT_FILES, (
        f"only {seen} tracked text files found (expected >= {_MIN_TEXT_FILES}) -- this "
        f"scan is mis-rooted at {REPO}, or git was unavailable, and read almost nothing."
    )


def test_every_allowlisted_file_still_needs_its_exemption() -> None:
    """An exemption outlives its subject in silence unless something checks."""
    for rel, why in _ALLOWED.items():
        path = REPO / rel
        assert path.is_file(), f"{rel} is allowlisted ({why}) but no longer exists -- drop the entry"
        blob = path.read_bytes()
        assert any(c in blob for c in _BANNED), (
            f"{rel} is allowlisted ({why}) but carries no control character any more -- "
            f"the exemption is stale and should be removed."
        )


def test_no_stray_control_characters() -> None:
    offenders, _ = _scan()
    assert not offenders, (
        "these text files contain control bytes where an escape sequence was almost "
        "certainly meant:\n"
        + "\n".join(f"    {path}  ({detail})" for path, detail in offenders)
        + "\n\nThe usual cause is text processed one layer too eagerly, turning `\b` into "
        "a backspace, `\a` into a bell and `\n` into a real newline. Nothing else here "
        "can see it: the file stays valid UTF-8 and parses fine, because the byte sits "
        "inside a string or a comment. In a REGEX it is worse than cosmetic -- the "
        "pattern still compiles, still runs, and quietly matches nothing.\n\n"
        "Write the escape (`\b`, `\a`), or add the file to _ALLOWED with the reason the "
        "literal byte is required."
    )
