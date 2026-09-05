"""No source file carries text that is already cp1251-mangled UTF-8.

`test_text_encoding_discipline` guards the **cause** — an `open()` / `read_text()`
with no `encoding=`, which on this machine decodes UTF-8 through cp1251. This
guards the **residue**: a file that has already been through that round trip and
saved back, where the corruption is now a string literal in the source and no
amount of fixing the I/O will undo it.

Found in `gateway/channels/telegram.py`: **12 lines, 13 occurrences**, every one a
string the bot sends to a user — the onboarding message, the photo caption, the
vision notes, the `📝 OCR` header, and three inline **button labels**, all reading
as `рџ“ќ`-style garbage in Telegram. `📝` is `\\xf0\\x9f\\x93\\x9d`; decoded as
cp1251 that is `рџ"ќ`, and re-saved as UTF-8 it stays that way forever.

⚠ The display lies in BOTH directions, so trust bytes and nothing else. Printing
the mangled text to a terminal whose code page is cp1251 re-encodes it back into
the original bytes, and a UTF-8 reader then shows a perfect `📝`. The corruption
is invisible exactly where you would look for it.

Detection is a round trip, not a blocklist: take each non-ASCII run, map it back
to bytes (cp1251, with the C1 range passed through — `\\x98` has no cp1251
character and survives as U+0098), and decode as UTF-8. Real prose does not
survive that; mangled emoji reconstructs perfectly. That precision is why this
can be a build gate rather than a warning.

⚠ You cannot QUOTE the mangled form inside a scanned root — this guard caught its
own registration comment in `scripts/ci-local.mjs` on the first full run, having
passed standalone because the comment was written afterwards. Describe the shape
in words there, or put the example somewhere outside `_ROOTS` (this docstring is
under `core/tests`, which is not scanned; `core/CHANGELOG.md` is not either).

⚠ Scope note: scans the tree and names no module of its own, so "tests for
changed modules" can never select it. Registered in `sourceGuardArgs` in
`scripts/ci-local.mjs`.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_ROOTS = (
    "core/navig", "core/tools", "core/docs", "plugins", "scripts",
    "apps", "services", "packages", "web", "registry", "docs", "tools",
)
_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".yaml", ".yml"})
#: `.archive` holds forge's retired extensions — dead reference code carrying 103
#: occurrences of this exact corruption, imported by nothing (even its tests live
#: there). Rewriting it would be churn in a diff nobody reads; this guard's job is
#: the code that ships.
_SKIP_PARTS = frozenset({
    "node_modules", ".dev", "build", "dist", "__pycache__", ".lab", ".archive",
})

_NON_ASCII_RUN = re.compile(r"[^\x00-\x7F]+")


def _as_original_bytes(run: str) -> bytes:
    out = bytearray()
    for ch in run:
        code = ord(ch)
        if code < 0x80 or 0x80 <= code <= 0x9F:
            # ASCII, or a C1 byte cp1251 leaves undefined — it survived the mangle
            # as its own code point (this is how `\x98` in `😄` comes back).
            out.append(code)
        else:
            out.extend(ch.encode("cp1251"))
    return bytes(out)


def recovered(run: str) -> str | None:
    """What *run* was before a cp1251 round trip, or None if it is not mangled."""
    try:
        text = _as_original_bytes(run).decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return None
    # Accept only a recovery that yields symbols/emoji. Without this a run of
    # ordinary Cyrillic prose could occasionally decode to *something* and the
    # guard would start rewriting legitimate text in its report.
    return text if text and all(ord(c) >= 0x2000 for c in text) else None


@lru_cache(maxsize=1)
def _files() -> tuple[Path, ...]:
    """Every scannable file under the roots.

    PRUNES as it walks rather than filtering afterwards. `rglob` descends into
    `node_modules` and `.dev` in full and only then discards them, which over
    `apps/` alone cost more than a minute — unaffordable for a guard that runs on
    every push. Cached because two tests need the same list.

    Skips are matched on parts RELATIVE to the root: `.dev` is in `_SKIP_PARTS`
    and every agent works in a worktree under `.dev/worktrees/<slug>/`, so an
    absolute-path match skipped the entire tree there — the scan returned 0 files
    and the anti-vacuity check below failed the gate for everyone working the
    documented way, while passing in the main checkout.
    """
    out: list[Path] = []
    for rel in _ROOTS:
        root = _REPO / rel
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_PARTS]
            here = Path(dirpath)
            out += [here / f for f in filenames if Path(f).suffix.lower() in _SUFFIXES]
    return tuple(out)


def scan_source(source: str) -> list[tuple[int, str, str]]:
    """`(line, mangled, recovered)` for every mangled run in *source*.

    Split out of the tree walk ON PURPOSE. With the tree clean, a blind pre-filter
    and a seeing one both report zero offenders, so no test over the real tree can
    fail when the filter regresses — mutation testing proved exactly that. This is
    the seam a synthetic test can drive.
    """
    # `isascii()` is the whole pre-filter, and it has to be. The previous one
    # tested for the EMOJI-plane lead pair at BOTH file and line level, so it
    # could only ever see 4-byte characters. Mangled `✅ ✓ — → ─` are 3-byte
    # and lead with a different letter entirely — invisible to it. 83 of them
    # sat in `gateway/channels/telegram.py` while this guard called that file
    # clean, the day after it was written to fix that same file.
    if source.isascii():
        return []
    out: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(source.splitlines(), 1):
        for match in _NON_ASCII_RUN.finditer(line):
            if fixed := recovered(match.group()):
                out.append((lineno, match.group(), fixed))
    return out


def _offenders() -> list[str]:
    found: list[str] = []
    for path in _files():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found += [
            f"{path.relative_to(_REPO)}:{lineno} — {bad!r} should be {good!r}"
            for lineno, bad, good in scan_source(source)
        ]
    return found


def test_no_source_file_carries_mangled_text() -> None:
    offenders = _offenders()
    assert not offenders, (
        "These literals are UTF-8 that was decoded as cp1251 and saved back, so they "
        "reach users as garbage. Replace each with the character shown:\n  "
        + "\n  ".join(offenders)
    )


def test_the_scan_actually_reads_the_tree() -> None:
    """A scan that reads nothing passes the assertion above for free."""
    files = _files()
    assert len(files) > 1000, f"only found {len(files)} files — are the roots wrong?"


def test_the_round_trip_recognises_mangling_and_leaves_real_text_alone() -> None:
    """Teeth: pinned to synthetic strings, so it keeps working at zero offenders."""
    # The exact shapes found in telegram.py, including the C1-passthrough case.
    assert recovered("рџ“ќ") == "📝"
    assert recovered("рџ‘Ђ") == "👀"
    assert recovered("рџ\x98„") == "😄", "the \\x98 C1 passthrough broke"

    # Real content must never be reported: Cyrillic prose, an intact emoji, ASCII.
    assert recovered("Интуиция это функция") is None
    assert recovered("📝") is None
    assert recovered("café") is None


def test_it_sees_THREE_byte_mangling_not_only_emoji() -> None:
    """The blindness that let 83 occurrences sit in a file this guard had just
    been used to clean.

    The first version pre-filtered on the EMOJI-plane lead pair — `\xf0\x9f` seen
    through cp1251 — at both file and line level. Every character below is 3-byte
    UTF-8 with a different lead, so none of them could ever be reached, and the
    guard reported the file clean the day after it was written to fix that file.
    """
    assert recovered("в”Ђ") == "─", "box drawing — 49 of these were invisible"
    assert recovered("в†’") == "→", "arrow — 17 were invisible"
    assert recovered("в‰¤") == "≤"
    assert recovered("вЂ”") == "—", "em dash — this is what the registry agent carried"
    assert recovered("вЂў") == "•"
    assert recovered("вњ…") == "✅"


def test_the_scan_prunes_instead_of_filtering() -> None:
    """A guard in `sourceGuardArgs` runs on every push, so its cost is part of its
    design. `rglob` descended into `node_modules` in full and discarded it after:
    147s. Pruning during the walk and caching the list: under 10s for the same
    roots. This pins the pruning, not the timing — a wall-clock assertion would be
    flaky on a loaded machine.
    """
    files = _files()
    # Parts RELATIVE to the repo, never the absolute path — the same rule `_files`
    # itself documents. Every agent works in a worktree under `.dev/worktrees/<slug>/`,
    # and `.dev` is in _SKIP_PARTS, so an absolute-path match reports that EVERY file
    # came from a skipped directory. That failed the gate for everyone working the
    # documented way while passing in the main checkout — the exact asymmetry this
    # guard was written to end, reintroduced one layer up in its own assertion.
    walked_into = [
        p for p in files if _SKIP_PARTS & set(p.relative_to(_REPO).parts)
    ]
    assert not walked_into, (
        f"a skipped directory was walked into and filtered afterwards: {walked_into[:3]}"
    )
    assert _files() is files, "the file list is not cached — two tests walk it"


def test_the_symbol_filter_is_load_bearing_and_deliberately_narrow() -> None:
    """`recovered()` accepts only results that are symbols. That is a real
    restriction, not decoration, and it needs a case that proves it.

    Mangled Cyrillic PROSE round-trips just as cleanly as a mangled emoji —
    `\u0420\u00b0` is bytes D0 B0, which is UTF-8 for `\u0430`. The filter rejects it
    because a report that rewrites ordinary letters is one nobody can trust, and
    the guard would then be arguing with every fixture in this repo that contains
    real Russian.

    Measured before accepting the cost: scanning every file under `_ROOTS` for
    runs of 4+ characters that round-trip to non-symbol text finds **zero**. So
    the narrowing hides nothing today — and if that ever changes, the fix is a
    separate, careful pass, not a widened filter here.
    """
    assert _as_original_bytes("\u0420\u00b0").decode("utf-8") == "\u0430", (
        "premise: this IS a clean round trip"
    )
    assert recovered("\u0420\u00b0") is None, "the symbol filter is not applied"
    assert scan_source("x = \u0420\u00b0\u00d0\u00b1\u00d0\u00b2\n") == []


def test_the_SCAN_reaches_three_byte_mangling_not_only_the_round_trip() -> None:
    """The pre-filter regression must fail a test, not just be argued about.

    `recovered()` being correct proves nothing about what the scan REACHES: with
    the tree clean, a blind filter and a seeing one both report zero offenders.
    This drives the scan over a line whose only mangling is 3-byte — exactly what
    the old filter skipped — so restoring that filter fails here.
    """
    line = "    # \u0432\u201d\u0402\u0432\u201d\u0402 Access Control\n"
    hits = scan_source(line)

    assert hits, "the scan did not reach 3-byte mangling — is the pre-filter back?"
    assert hits[0][2] == "\u2500\u2500", hits

    # …and a 4-byte one still lands, so one blindness was not traded for another.
    assert scan_source('n = "\u0440\u045f\u201c\u045c Edit"')[0][2] == "\U0001F4DD"

    # Clean sources cost nothing and report nothing.
    assert scan_source("plain ascii only\n") == []
    assert scan_source("# \u2500\u2500 Access Control\n") == []
