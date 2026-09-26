"""`core/CHANGELOG.md` merges by UNION — the rule in `.gitattributes` must stay wired.

Every session inserts its entry at the same line (the top of `[Unreleased] > ### Added`),
so two concurrent branches collide on every rebase, and a rebase here costs a full
pre-push gate. `merge=union` makes git keep both entries instead of leaving markers
(measured to produce exactly the hand resolution). This test asks git — not the file —
whether the attribute applies, so a rule that is present but mis-pathed fails too.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_core_changelog_merges_by_union() -> None:
    out = subprocess.run(
        ["git", "-C", str(_REPO), "check-attr", "merge", "core/CHANGELOG.md"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=True,
    ).stdout.strip()
    assert out.endswith(": merge: union"), (
        f"core/CHANGELOG.md has lost its union merge driver ({out!r}) — restore "
        "`core/CHANGELOG.md merge=union` in .gitattributes, or every concurrent "
        "changelog insertion conflicts on rebase again."
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")
def test_the_rule_is_narrow_by_design() -> None:
    """Union silently duplicates a line both sides EDIT; only the hot file earns that trade."""
    out = subprocess.run(
        ["git", "-C", str(_REPO), "check-attr", "merge", "plugins/navig-vault/CHANGELOG.md"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=True,
    ).stdout.strip()
    assert out.endswith(": merge: unspecified"), out


# ── the driver's one failure mode, caught mechanically ───────────────────────
#
# `merge=union` keeps BOTH sides of a conflicting hunk. For two insertions that is the
# hand resolution; for two EDITS of the same line it is the line twice, silently — the
# old and the new version, adjacent. Measured over the live file: 0 today in
# [Unreleased]; whole-file the same two shapes hit 3 + 3 ancient, legitimate lines, so
# the scope is the one block concurrent branches edit. Conflict markers are checked
# whole-file: a resolver that crashed before writing once committed three of them.

_MARKER = re.compile(r"^(<{7} |={7}$|>{7} )", re.M)


def _unreleased_block(text: str) -> str:
    start = text.index("## [Unreleased]")
    nxt = text.find("\n## ", start + 1)
    return text[start : nxt if nxt != -1 else len(text)]


def union_duplicates(text: str) -> list[str]:
    """Adjacent lines in [Unreleased] that are the union driver's signature.

    Two consecutive non-blank lines that are identical, or two consecutive entry lines
    (``- **``) that differ yet share their first 30 characters — one line, two versions.
    """
    lines = _unreleased_block(text).split("\n")
    found: list[str] = []
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        if not a.strip():
            continue
        if a == b:
            found.append(f"identical twice: {a[:70]!r}")
        elif a.startswith("- **") and b.startswith("- **") and a[:30] == b[:30]:
            found.append(f"same entry, two versions: {a[:70]!r}")
    return found


def test_unreleased_carries_no_union_duplicates() -> None:
    text = (_REPO / "core" / "CHANGELOG.md").read_text(encoding="utf-8")
    dups = union_duplicates(text)
    assert not dups, (
        "core/CHANGELOG.md [Unreleased] holds a line twice — the union merge driver kept "
        "both versions of a line two branches edited. Keep one:\n  " + "\n  ".join(dups)
    )
    assert not _MARKER.search(text), "core/CHANGELOG.md contains a merge-conflict marker"


def test_the_duplicate_detector_sees_both_shapes_and_nothing_else() -> None:
    clean = "# C\n\n## [Unreleased]\n\n### Added\n- **A.** one\n  more\n- **B.** two\n\n## [1.0]\n- **X.** x\n- **X.** x\n"
    assert union_duplicates(clean) == [], "an old release is out of scope"
    twice = clean.replace("- **B.** two\n", "- **B.** two\n- **B.** two\n")
    assert len(union_duplicates(twice)) == 1 and "identical twice" in union_duplicates(twice)[0]
    head = "- **Bravo, a header longer than thirty chars.**"  # real headers are; the prefix is 30
    edited = clean.replace("- **B.** two\n", f"{head} old wording\n{head} new wording\n")
    assert len(union_duplicates(edited)) == 1 and "two versions" in union_duplicates(edited)[0]
    blank_gap = clean.replace("- **B.** two\n", "- **B.** two\n\n- **B.** two\n")
    assert union_duplicates(blank_gap) == [], "only ADJACENT lines are the driver's signature"
