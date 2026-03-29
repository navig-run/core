#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
VERSION_PATTERN = re.compile(r'(?m)^(version\s*=\s*")(?P<v>\d+\.\d+\.\d+)(")\s*$')


def run_git(*args: str) -> str:
    cmd = ["git", *args]
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
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


def git_commit(version: str) -> None:
    run_git("add", "pyproject.toml")
    run_git("commit", "-m", f"chore(release): bump version to {version}")


def git_tag(version: str) -> str:
    tag_name = f"v{version}"
    ensure_tag_absent(tag_name)
    run_git("tag", "-a", tag_name, "-m", f"Release {tag_name}")
    return tag_name


def git_push_tag(tag_name: str) -> None:
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
    bump_parser.add_argument("--push", action="store_true", help="Push tag to origin (requires --tag)")
    bump_parser.set_defaults(command="bump")

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.command == "show":
        print(read_version())
        return 0

    if args.push and not args.tag:
        raise RuntimeError("--push requires --tag")

    ensure_branch_main()

    current = read_version()
    next_version = bump_version(current, args.level)
    old_version, new_version = write_version(next_version)
    print(f"Version: {old_version} -> {new_version}")

    if args.commit:
        git_commit(new_version)
        print(f"Committed version bump to {new_version}")

    tag_name = None
    if args.tag:
        tag_name = git_tag(new_version)
        print(f"Created tag: {tag_name}")

    if args.push and tag_name:
        git_push_tag(tag_name)
        print(f"Pushed tag: {tag_name}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
