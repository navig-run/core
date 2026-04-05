#!/usr/bin/env python3
"""
scripts/_version_sync.py — Propagate canonical version to all manifest files.

Reads the version from pyproject.toml and writes:
  - latest.json         (repo root — canonical)
  - config/latest.json  (mirror)

Optionally syncs to a sibling navig-www directory when the
NAVIG_DEV_SYNC=1 env-var is set or when the directory is detected.
This is non-fatal — a missing navig-www is silently skipped.

Usage
-----
  python scripts/_version_sync.py                    # use version in pyproject.toml
  python scripts/_version_sync.py --version 2.5.0   # explicit version (post-bump)
  python scripts/_version_sync.py --dry-run          # preview only, no writes
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
LATEST_JSON_PATH = REPO_ROOT / "latest.json"
CONFIG_LATEST_JSON_PATH = REPO_ROOT / "config" / "latest.json"

DOWNLOAD_URL_TEMPLATE = (
    "https://github.com/navig-run/core/releases/download/"
    "v{version}/navig-{version}.tar.gz"
)
CHANGELOG_URL = "https://github.com/navig-run/core/releases"

_VERSION_RE = re.compile(r'(?m)^version\s*=\s*"(?P<v>\d+\.\d+\.\d+)"\s*$')


def read_pyproject_version() -> str:
    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    match = _VERSION_RE.search(content)
    if not match:
        raise RuntimeError("Could not find version = ... in pyproject.toml")
    return match.group("v")


def build_manifest(version: str) -> dict:
    return {
        "version": version,
        "channel": "stable",
        "download_url": DOWNLOAD_URL_TEMPLATE.format(version=version),
        "changelog_url": CHANGELOG_URL,
        "released_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def write_json(path: Path, data: dict, dry_run: bool) -> None:
    payload = json.dumps(data, indent=2) + "\n"
    try:
        display = str(path.relative_to(REPO_ROOT))
    except ValueError:
        display = str(path)
    if dry_run:
        print(f"  [dry-run] would write {display}:\n{payload}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    print(f"  \u2705 wrote {display}")


def maybe_sync_www(version: str, dry_run: bool) -> None:
    """Best-effort sync to sibling navig-www repo (non-fatal)."""
    # Respect opt-in env var or auto-detect sibling directory
    env_sync = os.environ.get("NAVIG_DEV_SYNC", "").strip()
    www_root = REPO_ROOT.parent / "navig-www"
    if not (env_sync == "1" or www_root.is_dir()):
        return

    target = www_root / "public" / "latest.json"
    if not target.parent.is_dir():
        # navig-www exists but doesn't have the expected structure — skip
        return

    manifest = build_manifest(version)
    try:
        write_json(target, manifest, dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️  navig-www sync skipped: {exc}", file=sys.stderr)


def run(version: str | None = None, dry_run: bool = False) -> str:
    resolved = version or read_pyproject_version()
    manifest = build_manifest(resolved)

    print(f"Syncing version {resolved} to manifests:")
    write_json(LATEST_JSON_PATH, manifest, dry_run)
    write_json(CONFIG_LATEST_JSON_PATH, manifest, dry_run)
    maybe_sync_www(resolved, dry_run)
    return resolved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync version manifests (latest.json) from pyproject.toml"
    )
    parser.add_argument(
        "--version", "-V",
        default=None,
        help="Explicit version to write (default: read from pyproject.toml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be written without making changes",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        resolved = run(version=args.version, dry_run=args.dry_run)
        if args.dry_run:
            print(f"\nDry-run complete. Version: {resolved} (no files changed)")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
