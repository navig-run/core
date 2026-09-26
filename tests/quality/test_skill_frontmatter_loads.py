"""Guard: a shipped skill's declared frontmatter must actually reach the loader.

The bug this exists for
-----------------------
Fifteen builtin skills wrapped their whole file in a ```skill code fence:

    ```skill
    ---
    name: iperf3-network-test
    description: Run iperf3 network speed tests - client or server mode
    user-invocable: true
    navig-commands: [...]
    ---

    # iperf3 Network Speed Test
    ...
    ```

`_load_frontmatter` reads frontmatter only at byte 0 (``if not text.startswith("---"):
return {}, text``) and the loader then falls back to "plain SKILL.md -- id/name derived
from the folder name". So every declared field was silently discarded. Measured with the
real loader before the fix:

    name        = 'iperf3 Network Speed Test'   (from the body heading, not the frontmatter)
    description = ''                            (the file declares a full sentence)
    navig-commands, user-invocable, risk-level: gone

An empty description is not cosmetic. Skills are model-invoked: the description is what
the agent matches on to decide whether a skill applies. These fifteen shipped in every
wheel, listed as present, and were invisible to the model.

Why nothing else caught it
--------------------------
The file is valid UTF-8, valid Markdown, and parses as YAML once you strip the fence, so
no encoding, lint or data-integrity check sees anything wrong. `parse_skill_file` returns
a Skill rather than raising -- the degraded result IS its documented fallback. Only
comparing what the file DECLARES against what the loader RETURNS shows the loss.

Scope
-----
Behavioural, not textual: any file declaring a `description:` must yield a non-empty
description from the real loader. That catches this bug and any future way frontmatter
stops reaching the parser, rather than pattern-matching the one fence that caused it.

A skill with no `description:` at all is a different, supported shape (plain Markdown,
id/name from the folder) and is not the subject here. Measured: 52 shipped skill files,
51 declare a description and load it, 1 declares none.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# Every tree whose skills reach a user: the builtin store ships in the wheel, `registry/`
# is what `navig install add` clones, and `plugins/*` skills ship with their plugin. The
# fence bug was found in the builtin store only -- measured, the other 46 files are clean
# -- so these roots are a floor placed before something falls through, not a fix.
_ROOTS = (
    "core/navig/builtin/skills",
    "core/navig/skills",
    "registry",
    "plugins",
)

# `.lab/` is a vendored research corpus carrying community SKILL.md files that are not
# ours to validate; `.dev/` holds worktrees of this same repo, which would double every
# count. Matched against the REPO-RELATIVE path -- matching absolute parts skips the repo
# itself whenever the checkout lives under one of these names, which is every
# `.dev/worktrees/<slug>`.
_SKIP_DIRS = {
    ".git", ".lab", ".dev", "node_modules", "__pycache__",
    "dist", "build", "target", ".venv", "venv",
}

# A `description:` at the start of a line, with a value. Deliberately not anchored to a
# frontmatter block: if the key is declared anywhere the loader should be seeing, a lost
# value is worth reporting.
_DECLARES_DESCRIPTION = re.compile(r"^description:\s*\S", re.MULTILINE)

# Measured at 98 skill files across the four roots, 90 of which declare a description
# (52/51 builtin, 26/19 registry, 18/18 plugins, 2/2 core). The floors sit well below
# that so churn cannot trip them, and far above the zero a mis-rooted scan produces.
_MIN_SKILL_FILES = 70
_MIN_DECLARING = 60


def _skill_files() -> list[Path]:
    """Every shipped skill file, deduplicated for a case-insensitive filesystem.

    On Windows `rglob("SKILL.md")` and `rglob("skill.md")` return the SAME file twice, so
    a naive scan reports 104 files where there are 52 -- and any per-file count drawn from
    it is double. Dedupe on the resolved lowercase path.
    """
    seen: dict[str, Path] = {}
    for root in _ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in list(base.rglob("SKILL.md")) + list(base.rglob("skill.md")):
            if _SKIP_DIRS & set(path.relative_to(REPO).parts):
                continue
            seen[str(path.resolve()).lower()] = path
    return sorted(seen.values(), key=lambda p: str(p).lower())


@lru_cache(maxsize=1)
def _scan() -> tuple[tuple[str, ...], int, int]:
    """((offender, ...), files seen, files declaring a description)."""
    from navig.skills.loader import parse_skill_file

    offenders: list[str] = []
    files = _skill_files()
    declaring = 0
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not _DECLARES_DESCRIPTION.search(text):
            continue
        declaring += 1
        skill = parse_skill_file(path)
        if skill is None or not (skill.description or "").strip():
            offenders.append(path.relative_to(REPO).as_posix())
    return tuple(offenders), len(files), declaring


def test_the_scan_actually_reads_the_skills() -> None:
    """Stated as a presence: a scan that found nothing reports no offenders."""
    assert (REPO / "core" / "navig" / "builtin" / "skills").is_dir(), (
        f"{REPO} does not look like the repo root; every count below it is meaningless"
    )
    _, seen, declaring = _scan()
    assert seen >= _MIN_SKILL_FILES, (
        f"only {seen} skill files found across {_ROOTS} (expected >= {_MIN_SKILL_FILES}) -- "
        f"this scan read almost nothing."
    )
    assert declaring >= _MIN_DECLARING, (
        f"only {declaring} of {seen} skill files declare a description; the check has "
        f"almost no subjects left."
    )


def test_a_declared_description_reaches_the_loader() -> None:
    offenders, _, _ = _scan()
    assert not offenders, (
        "these skills declare a `description:` that the loader does not receive:\n"
        + "\n".join(f"    {o}" for o in offenders)
        + "\n\nThe loader reads frontmatter only at byte 0 and otherwise falls back to "
        "deriving id/name from the folder, discarding every declared field. The known "
        "cause is a leading ```skill code fence wrapping the frontmatter -- valid "
        "Markdown, valid YAML once unwrapped, and invisible to every other check.\n\n"
        "Skills are model-invoked: the description is what the agent matches on, so a "
        "lost one makes the skill ship, list as present, and never be chosen."
    )
