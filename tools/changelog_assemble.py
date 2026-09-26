#!/usr/bin/env python3
"""Assemble ``core/changelog.d/*.md`` fragments into ``core/CHANGELOG.md`` → ``[Unreleased]``.

Why fragments
-------------
``core/CHANGELOG.md`` is the hottest file in the repo (189 commits in 60 days) and every
change inserted its entry at the SAME line — the top of ``[Unreleased] > ### Added``. Two
branches doing that collide on every rebase, and GitHub's server-side merge does not run
merge drivers, so ``merge=union`` (which makes the LOCAL rebase clean) still left the PR
"conflicting" and cost a rebase + a full pre-push gate per collision — measured: four in
three hours across two PRs, and no fixed insertion slot escapes it.

A fragment is one NEW file per change under ``core/changelog.d/``, so no two changes ever
touch the same path and GitHub merges them without a rebase. This script folds them into
the changelog — at release, or whenever someone wants the Unreleased section readable.

Fragment contract (checked by ``--check`` and by tests/quality/test_changelog_fragments.py)
-------------------------------------------------------------------------------------------
* filename ``<slug>.<kind>.md`` — slug ``[a-z0-9-]+``, kind one of ``added changed
  deprecated removed fixed security`` (Keep a Changelog's headings, lower-cased);
* body: one or more Markdown list entries in the house shape — first line starts with
  ``- **``, continuation lines indented two spaces — exactly as they will appear;
* UTF-8, LF, no conflict markers.

Usage (from ``core/``)::

    python tools/changelog_assemble.py --check            # validate fragments, change nothing
    python tools/changelog_assemble.py --dry-run          # show what would move where
    python tools/changelog_assemble.py                    # fold fragments in, delete them
    python tools/changelog_assemble.py --release 3.26.0   # fold, then rotate [Unreleased]
                                                          #   under "## [3.26.0] — <today>"

Assembled entries go to the TOP of their ``### <Kind>`` section (the changelog reads
newest-first), ordered by slug so the result is deterministic; a section that does not
exist yet is created in Keep a Changelog order. Idempotent: with no fragments the file is
left byte-identical. stdlib only — this runs on a checkout with no venv.

Release rotation (``--release``)
--------------------------------
The 3.25.0 release rotated ``[Unreleased]`` under the version heading BY HAND, in the
release commit; ``version_bump.py`` — the documented release path — touched only the
version manifests, so a bump would have shipped a tag whose changelog still said
"Unreleased" and left every fragment on disk. ``--release X.Y.Z`` is that rotation as a
tool: fold fragments, move every section of ``[Unreleased]`` under ``## [X.Y.Z] — date``
(the em dash and shape are byte-identical to the existing headings), and leave a fresh
``[Unreleased]`` carrying the template comments with the new version. It REFUSES an empty
release (a version heading over nothing is a lie — ``version_bump.py --no-changelog`` is
the deliberate hatch) and a version already present, unless ``[Unreleased]`` is empty too,
in which case it is a no-op: ``release(release(x)) == release(x)``.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

KINDS = ("added", "changed", "deprecated", "removed", "fixed", "security")
HEADINGS = {k: f"### {k.capitalize()}" for k in KINDS}
_NAME = re.compile(r"^(?P<slug>[a-z0-9][a-z0-9-]*)\.(?P<kind>[a-z]+)\.md$")
_MARKERS = re.compile(r"^(<{7}|={7}|>{7})", re.M)
README = "README.md"


@dataclass(frozen=True)
class Fragment:
    path: Path
    slug: str
    kind: str
    body: str  # normalised: LF, single trailing newline


@dataclass(frozen=True)
class Problem:
    path: Path
    why: str


def fragments_dir(core_dir: Path) -> Path:
    return core_dir / "changelog.d"


def load_fragments(core_dir: Path) -> tuple[list[Fragment], list[Problem]]:
    """Every ``*.md`` under changelog.d except README, parsed; problems reported, not raised."""
    d = fragments_dir(core_dir)
    if not d.is_dir():
        return [], []
    frags: list[Fragment] = []
    problems: list[Problem] = []
    for p in sorted(d.iterdir()):
        if not p.is_file() or p.name == README or p.name.startswith("."):
            continue
        m = _NAME.match(p.name)
        if not m:
            problems.append(Problem(p, "name must be <slug>.<kind>.md (slug: a-z 0-9 -)"))
            continue
        kind = m.group("kind")
        if kind not in KINDS:
            problems.append(Problem(p, f"kind {kind!r} is not one of {', '.join(KINDS)}"))
            continue
        try:
            raw = p.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append(Problem(p, f"not UTF-8: {exc}"))
            continue
        body = raw.replace("\r\n", "\n").strip("\n")
        if not body.strip():
            problems.append(Problem(p, "empty"))
            continue
        if _MARKERS.search(body):
            problems.append(Problem(p, "contains a merge-conflict marker"))
            continue
        if not body.startswith("- **"):
            problems.append(Problem(p, "must start with `- **` (a house-style list entry)"))
            continue
        bad = [
            ln
            for ln in body.split("\n")[1:]
            if ln and not (ln.startswith("- ") or ln.startswith("  "))
        ]
        if bad:
            problems.append(
                Problem(p, f"continuation lines must be indented two spaces: {bad[0][:60]!r}")
            )
            continue
        frags.append(Fragment(p, m.group("slug"), kind, body + "\n"))
    return frags, problems


def _unreleased_span(text: str) -> tuple[int, int]:
    """[start, end) of the ``## [Unreleased]`` block, up to the next ``## `` heading."""
    m = re.search(r"^## \[Unreleased\][^\n]*\n", text, re.M)
    if not m:
        raise ValueError("core/CHANGELOG.md has no '## [Unreleased]' heading")
    nxt = re.search(r"^## ", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    return m.start(), end


def assemble_text(changelog: str, frags: list[Fragment]) -> str:
    """The changelog with every fragment folded into its section. Pure; no I/O."""
    if not frags:
        return changelog
    text = changelog.replace("\r\n", "\n")
    start, end = _unreleased_span(text)
    block = text[start:end]

    by_kind: dict[str, list[Fragment]] = {k: [] for k in KINDS}
    for f in frags:
        by_kind[f.kind].append(f)

    for kind in KINDS:
        entries = sorted(by_kind[kind], key=lambda f: f.slug)
        if not entries:
            continue
        payload = "".join(f.body for f in entries)
        heading = HEADINGS[kind]
        hm = re.search(rf"^{re.escape(heading)}[ \t]*\n", block, re.M)
        if hm:
            # Top of the existing section: the changelog reads newest-first.
            block = block[: hm.end()] + payload + block[hm.end() :]
            continue
        # Create the section in canonical order: right before the first later kind that
        # exists, else at the end of the Unreleased block.
        insert_at = len(block.rstrip("\n")) + 1
        for later in KINDS[KINDS.index(kind) + 1 :]:
            lm = re.search(rf"^{re.escape(HEADINGS[later])}[ \t]*\n", block, re.M)
            if lm:
                insert_at = lm.start()
                break
        section = f"{heading}\n{payload}\n"
        before = block[:insert_at]
        if before and not before.endswith("\n\n"):
            before = before.rstrip("\n") + "\n\n"
        block = before + section + block[insert_at:]

    if not block.endswith("\n\n"):
        block = block.rstrip("\n") + "\n\n"
    return text[:start] + block + text[end:]


# ── release rotation ─────────────────────────────────────────────────────────

_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# The template comments a fresh [Unreleased] carries. Regenerated on every rotation so the
# `git log v<prev>..HEAD` hint names the version just released, not the one before it.
_TEMPLATE = (
    "<!-- Do NOT add entries here by hand: drop a fragment in changelog.d/<slug>.<kind>.md"
    " (see changelog.d/README.md) -->\n"
    "<!-- and fold them in with `npm run changelog:assemble`. Direct edits of this block"
    " collide on every rebase. -->\n"
    "<!-- This file merges by UNION (.gitattributes): two branches inserting at the same"
    " line keep both entries on rebase. -->\n"
    '<!-- Run: git log v{version}..HEAD --pretty="- %s (%h)" to auto-generate draft'
    " entries. -->\n"
)


class NothingToRelease(ValueError):
    """[Unreleased] holds no section: a version heading over nothing would be a lie."""


class AlreadyReleased(ValueError):
    """``## [version]`` is present AND [Unreleased] still holds entries — ambiguous."""


def unreleased_sections(changelog: str) -> str:
    """The ``### …`` sections of [Unreleased] — template comments and blank lead stripped."""
    text = changelog.replace("\r\n", "\n")
    start, end = _unreleased_span(text)
    block = text[start:end]
    first = re.search(r"^### ", block, re.M)
    return block[first.start():].strip("\n") + "\n" if first else ""


def version_section(changelog: str, version: str) -> str | None:
    """The body under ``## [version] …`` up to the next ``## `` heading, or None."""
    text = changelog.replace("\r\n", "\n")
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n", text, re.M)
    if not m:
        return None
    nxt = re.search(r"^## ", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    return text[m.end():end].strip("\n") + "\n"


# The GitHub Release body has ONE writer. `tools/release.sh` (the hand path) and
# `.github/workflows/release.yml` (the tag path) used to compose it separately — the script
# pasted the raw changelog block, the workflow an install snippet plus auto-generated notes
# — two shapes of one public page, whichever ran last winning (the same class #1480 closed
# for latest.json). Both now print THIS and hand it to `gh` / the release action.
_INSTALL_BLOCK = (
    "## Install / Update\n\n"
    "```bash\n"
    "# New install\n"
    "pip install navig=={version}\n\n"
    "# Self-update (if already installed)\n"
    "navig update\n"
    "```\n"
)


# GitHub refuses a release body over 125,000 characters. The 3.25.0 block is 288 KB and
# 3.24.0's 148 KB — the hand path's `gh release create --notes-file` would have been
# refused for both (and was: the owner created 3.25.0's release by hand). Leave room for
# the auto-generated "What's Changed" list `gh`/the action append after this body.
RELEASE_BODY_LIMIT = 100_000
_ENTRY_HEADLINE = re.compile(r"^- \*\*(?P<head>.+?)\*\*", re.M)
_CHANGELOG_URL = "https://github.com/navig-run/core/blob/v{version}/CHANGELOG.md"


def _digest(section: str, version: str) -> str:
    """Every entry's bold headline under its ``### Kind``, with a link to the full block.

    The house entry shape is ``- **One sentence that says what changed.** detail…`` — the
    headline alone is the release note; the detail is the changelog's job.
    """
    out: list[str] = []
    entries = 0
    for line in section.splitlines():
        if line.startswith("### "):
            if out:
                out.append("")
            out.append(line)
            continue
        m = _ENTRY_HEADLINE.match(line)
        if m:
            entries += 1
            out.append(f"- {m.group('head')}")
    body = "\n".join(out).strip("\n") + "\n"
    return (
        f"{body}\n_{entries} entries — headlines only; the full notes are in "
        f"[CHANGELOG.md]({_CHANGELOG_URL.format(version=version)})._\n"
    )


def release_notes(changelog: str, version: str, limit: int = RELEASE_BODY_LIMIT) -> str:
    """The GitHub Release body for *version*: install block + that version's changelog block.

    The full block when it fits in *limit* bytes; otherwise the headline digest, so the
    body is never refused. Raises ``ValueError`` when the changelog has no ``## [version]``
    block — a release with no notes is stopped here, not discovered on the Releases page.
    """
    section = version_section(changelog, version)
    if section is None:
        raise ValueError(
            f"CHANGELOG.md has no '## [{version}]' block - rotate [Unreleased] first "
            f"(python tools/changelog_assemble.py --release {version})"
        )
    head = _INSTALL_BLOCK.format(version=version) + "\n## What changed\n\n"
    full = head + section
    if len(full.encode("utf-8")) <= limit:
        return full
    return head + _digest(section, version)


def release_text(changelog: str, version: str, date: str) -> tuple[str, bool]:
    """Rotate [Unreleased] under ``## [version] — date``. Pure; no I/O.

    Returns ``(text, rotated)``: ``rotated`` is False when the heading already exists
    and [Unreleased] is empty — the idempotent re-run — and the text is unchanged.
    """
    if not _VERSION.match(version):
        raise ValueError(f"version must be X.Y.Z, got {version!r}")
    if not _DATE.match(date):
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")
    text = changelog.replace("\r\n", "\n")
    start, end = _unreleased_span(text)
    sections = unreleased_sections(text)
    heading = f"## [{version}] — {date}"
    present = re.search(rf"^## \[{re.escape(version)}\]", text, re.M) is not None
    if present and not sections:
        return text, False
    if present:
        raise AlreadyReleased(
            f"## [{version}] is already in CHANGELOG.md and [Unreleased] still holds entries —"
            " fold them under that heading by hand, or release the next version"
        )
    if not sections:
        raise NothingToRelease(
            "[Unreleased] holds no entries and there are no fragments — nothing to put under"
            f" ## [{version}]"
        )
    fresh = "## [Unreleased]\n\n" + _TEMPLATE.format(version=version) + "\n"
    released = f"{heading}\n\n{sections}\n"
    return text[:start] + fresh + released + text[end:], True


def release_changelog(core_dir: Path, version: str, date: str, *, dry_run: bool = False) -> dict:
    """Fold fragments, then rotate — the whole release step, for ``version_bump.py``.

    Raises ``ValueError`` (``NothingToRelease`` / ``AlreadyReleased`` / a malformed fragment
    or version) rather than printing, so the caller decides how loud to be. Returns
    ``{"fragments": [...names], "rotated": bool, "heading": str}``.
    """
    frags, problems = load_fragments(core_dir)
    if problems:
        raise ValueError(
            "invalid changelog fragment(s): "
            + "; ".join(f"{p.path.name}: {p.why}" for p in problems)
        )
    changelog = core_dir / "CHANGELOG.md"
    original = changelog.read_text(encoding="utf-8")
    assembled = assemble_text(original, frags)
    rotated_text, rotated = release_text(assembled, version, date)
    if not dry_run:
        if rotated_text != original.replace("\r\n", "\n"):
            changelog.write_text(rotated_text, encoding="utf-8", newline="\n")
        for f in frags:
            f.path.unlink()
    return {
        "fragments": [f.path.name for f in frags],
        "rotated": rotated,
        "heading": f"## [{version}] — {date}",
        # For printing: the heading carries an em dash, and a release tool must not
        # depend on the operator's console code page (cp1251 killed the first cut).
        "version": version,
        "date": date,
    }


def _print_problems(problems: list[Problem], core_dir: Path) -> None:
    for pr in problems:
        try:
            rel = pr.path.relative_to(core_dir.parent)
        except ValueError:
            rel = pr.path
        print(f"  x {rel}: {pr.why}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="validate fragments; write nothing")
    ap.add_argument("--dry-run", action="store_true", help="show the plan; write nothing")
    ap.add_argument(
        "--release", metavar="X.Y.Z",
        help="fold fragments, then rotate [Unreleased] under '## [X.Y.Z] — <date>'",
    )
    ap.add_argument(
        "--date", metavar="YYYY-MM-DD", help="release date for --release (default: today, UTC)"
    )
    ap.add_argument(
        "--release-notes", metavar="X.Y.Z",
        help="print the GitHub Release body for X.Y.Z (install block + its changelog block) to stdout",
    )
    ap.add_argument(
        "--core", type=Path, default=Path(__file__).resolve().parent.parent,
        help="path to core/ (default: this script's parent's parent)",
    )
    args = ap.parse_args(argv)
    core_dir: Path = args.core
    changelog = core_dir / "CHANGELOG.md"

    if args.release_notes is not None:
        try:
            body = release_notes(changelog.read_text(encoding="utf-8"), args.release_notes)
        except (ValueError, OSError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        # The body is UTF-8 by contract (the changelog is); bypass the console codec so a
        # cp1251 terminal cannot mangle an em dash on its way into the notes file.
        sys.stdout.buffer.write(body.encode("utf-8"))
        sys.stdout.flush()
        return 0

    # `is not None`, not truthiness: `--release ""` must be refused as a bad version, not
    # silently downgraded to a plain assemble.
    if args.release is not None:
        import datetime as _dt

        date = args.date or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
        try:
            result = release_changelog(core_dir, args.release, date, dry_run=args.dry_run)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 1
        for name in result["fragments"]:
            print(f"  folded     {name}")
        verb = "would rotate" if args.dry_run else "rotated"
        if result["rotated"]:
            print(f"OK: {verb} [Unreleased] under ## [{result['version']}] ({result['date']})")
        else:
            print(f"OK: ## [{result['version']}] already present and [Unreleased] is empty - nothing to do")
        return 0

    frags, problems = load_fragments(core_dir)
    if problems:
        print(f"changelog.d: {len(problems)} invalid fragment(s)")
        _print_problems(problems, core_dir)
        return 1
    if args.check:
        print(f"changelog.d: {len(frags)} fragment(s), all valid")
        return 0
    if not frags:
        print("changelog.d: nothing to assemble")
        return 0

    original = changelog.read_text(encoding="utf-8")
    try:
        assembled = assemble_text(original, frags)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    for f in sorted(frags, key=lambda f: (KINDS.index(f.kind), f.slug)):
        print(f"  {f.kind:<10} {f.path.name}")
    if args.dry_run:
        print(f"dry run: {len(frags)} fragment(s) would be folded into CHANGELOG.md [Unreleased]")
        return 0

    changelog.write_text(assembled, encoding="utf-8", newline="\n")
    for f in frags:
        f.path.unlink()
    print(f"OK: folded {len(frags)} fragment(s) into CHANGELOG.md [Unreleased]; fragments removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
