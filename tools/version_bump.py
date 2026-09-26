#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Allow importing sibling script without a package __init__
sys.path.insert(0, str(Path(__file__).parent))
from _version_sync import run as _sync_manifests  # noqa: E402
from changelog_assemble import release_changelog  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
VERSION_PATTERN = re.compile(r'(?m)^(version\s*=\s*")(?P<v>\d+\.\d+\.\d+)(")\s*$')


def run_git(*args: str) -> str:
    cmd = ["git", *args]
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def read_version() -> str:
    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(content)
    if not match:
        raise RuntimeError("Could not find [project] version in pyproject.toml")
    return match.group("v")


def write_version(new_version: str) -> tuple[str, str]:
    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(content)
    if not match:
        raise RuntimeError("Could not find [project] version in pyproject.toml")

    old_version = match.group("v")
    updated = VERSION_PATTERN.sub(rf'\g<1>{new_version}\3', content, count=1)
    PYPROJECT_PATH.write_text(updated, encoding="utf-8")
    return old_version, new_version


def bump_version(current: str, kind: str) -> str:
    major, minor, patch = [int(part) for part in current.split(".")]
    if kind == "patch":
        patch += 1
    elif kind == "minor":
        minor += 1
        patch = 0
    elif kind == "major":
        major += 1
        minor = 0
        patch = 0
    else:
        raise RuntimeError(f"Unsupported bump kind: {kind}")
    return f"{major}.{minor}.{patch}"


def ensure_branch_main() -> None:
    branch = run_git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        raise RuntimeError(f"Releases must run on 'main'. Current branch: {branch}")


def ensure_tag_absent(tag_name: str) -> None:
    local_tags = run_git("tag", "-l", tag_name)
    if local_tags.strip() == tag_name:
        raise RuntimeError(f"Tag {tag_name} already exists locally")
    remote_check = run_git("ls-remote", "--tags", "origin", tag_name)
    if remote_check.strip():
        raise RuntimeError(f"Tag {tag_name} already exists on origin")


def rotate_changelog(version: str, *, dry_run: bool = False) -> dict:
    """Fold ``changelog.d/`` fragments and rotate ``[Unreleased]`` under ``## [version]``.

    The 3.25.0 release did this by hand in the release commit; this path did not, so a
    bump shipped a tag whose changelog still said "Unreleased" and left every fragment on
    disk. Delegates to ``changelog_assemble.release_changelog``, which refuses an empty
    release (``--no-changelog`` is the deliberate hatch) and a version already present.
    """
    import datetime as _dt

    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    try:
        return release_changelog(REPO_ROOT, version, today, dry_run=dry_run)
    except ValueError as exc:
        # main() reports RuntimeError as a one-line error; a ValueError would traceback.
        raise RuntimeError(
            f"changelog: {exc} (pass --no-changelog only if this release truly has no entries)"
        ) from exc


def git_commit(version: str, *, changelog: bool = True) -> None:
    paths = ["pyproject.toml", "latest.json"]
    if changelog:
        paths.append("CHANGELOG.md")
    # Monorepo: the website version files (synced by _version_sync via the canonical
    # Node script) live at ../web/www — include them so the release commit carries the
    # site version too. Skipped in a standalone-core checkout where web/www is absent.
    for www_rel in ("../web/www/content/copy.ts", "../web/www/public/latest.json"):
        if (REPO_ROOT / www_rel).is_file():
            paths.append(www_rel)
    for path in paths:
        run_git("add", path)
    if changelog and (REPO_ROOT / "changelog.d").is_dir():
        # The fragments the rotation consumed are deletions; `-A` scoped to the one
        # directory stages exactly those and nothing else in the tree.
        run_git("add", "-A", "changelog.d")
    run_git("commit", "-m", f"chore(release): bump version to {version}")


def git_tag(version: str) -> str:
    tag_name = f"v{version}"
    ensure_tag_absent(tag_name)
    run_git("tag", "-a", tag_name, "-m", f"Release {tag_name}")
    return tag_name


def git_push_tag(tag_name: str) -> None:
    """Push the release COMMIT, then the tag.

    The tag alone used to be pushed: the commit it points at reached origin as an object
    but never as part of main, so origin/main still said the OLD version and carried none
    of the changelog rotation — the next bump from a synced checkout would have bumped the
    same version again. A tag whose commit is not on main is half a release.
    """
    run_git("push", "origin", "main")
    run_git("push", "origin", tag_name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bump pyproject version and optionally tag/push")
    sub = parser.add_subparsers(dest="command", required=True)

    show_parser = sub.add_parser("show", help="Print current project version")
    show_parser.set_defaults(command="show")

    bump_parser = sub.add_parser("bump", help="Bump version by level")
    bump_parser.add_argument("level", choices=["patch", "minor", "major"])
    bump_parser.add_argument("--commit", action="store_true", help="Commit pyproject version change")
    bump_parser.add_argument("--tag", action="store_true", help="Create annotated git tag")
    bump_parser.add_argument(
        "--push", action="store_true",
        help="Push the release commit to origin main, then the tag (requires --tag)",
    )
    bump_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the next version without writing files or creating git artifacts",
    )
    bump_parser.add_argument(
        "--no-changelog",
        action="store_true",
        help=(
            "Skip folding changelog.d/ fragments and rotating [Unreleased] under the new "
            "version. Only for a release that genuinely carries no user-facing entries — "
            "without it an empty [Unreleased] refuses the bump."
        ),
    )
    bump_parser.set_defaults(command="bump")

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.command == "show":
        print(read_version())
        return 0

    if args.push and not args.tag:
        raise RuntimeError("--push requires --tag")

    if args.dry_run and (args.commit or args.tag or args.push):
        raise RuntimeError("--dry-run cannot be combined with --commit/--tag/--push")

    ensure_branch_main()

    current = read_version()
    next_version = bump_version(current, args.level)

    if args.dry_run:
        print(f"Version (dry-run): {current} -> {next_version}")
        if not args.no_changelog:
            plan = rotate_changelog(next_version, dry_run=True)  # raises: nothing to release
            print(
                f"Changelog (dry-run): {len(plan['fragments'])} fragment(s) folded; "
                f"[Unreleased] -> ## [{plan['version']}] ({plan['date']})"
            )
        return 0

    # The changelog goes FIRST: its refusals (empty release, version already present, a
    # malformed fragment) must fire before pyproject.toml has been rewritten, or a refused
    # bump leaves a half-done release in the working tree.
    rotation = None
    if not args.no_changelog:
        rotation = rotate_changelog(next_version)

    old_version, new_version = write_version(next_version)
    print(f"Version: {old_version} -> {new_version}")
    if rotation is not None:
        print(
            f"Changelog: {len(rotation['fragments'])} fragment(s) folded; "
            f"[Unreleased] -> ## [{rotation['version']}] ({rotation['date']})"
        )

    _sync_manifests(version=new_version)

    if args.commit:
        git_commit(new_version, changelog=rotation is not None)
        print(f"Committed version bump to {new_version}")

    tag_name = None
    if args.tag:
        tag_name = git_tag(new_version)
        print(f"Created tag: {tag_name}")

    if args.push and tag_name:
        git_push_tag(tag_name)
        print(f"Pushed main and tag: {tag_name}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
