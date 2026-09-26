#!/usr/bin/env python3
"""
tools/_version_sync.py — Propagate canonical version to all manifest files.

Reads the version from pyproject.toml and writes:
  - latest.json         (repo root — the one release manifest)

Also syncs the website-visible version (web/www: content/copy.ts's
siteConfig.version + public/latest.json) via the canonical Node script, when the
in-repo ``web/www`` (monorepo) or a legacy sibling ``navig-www`` is present. This is
best-effort and non-fatal — a missing web/www or Node is skipped, never failing a
release.

Usage
-----
  python tools/_version_sync.py                    # use version in pyproject.toml
  python tools/_version_sync.py --version 2.5.0   # explicit version (post-bump)
  python tools/_version_sync.py --dry-run          # preview only, no writes
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
LATEST_JSON_PATH = REPO_ROOT / "latest.json"

PYPI_URL_TEMPLATE = "https://pypi.org/project/navig/{version}/"
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


# The one schema. Every writer of latest.json — this tool (hand releases), release.yml
# (tag releases), and web/www's copy — produces exactly these keys. They used to differ:
# the workflow dropped three of them and wrote a datetime where this wrote a date.
MANIFEST_KEYS = ("version", "channel", "pypi", "download_url", "changelog_url", "released_at")


def _asset_exists(url: str, timeout: float = 5.0) -> bool:
    """HEAD the GitHub Release asset. False on 404, timeout, or offline - a URL that
    could not be verified is not emitted (a link that 404s is worse than none)."""
    if os.environ.get("NAVIG_VERSION_SYNC_OFFLINE"):
        return False
    import urllib.request  # noqa: PLC0415

    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "navig-version-sync"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
            return 200 <= resp.status < 400
    except Exception:  # noqa: BLE001 - any failure means "not verifiable", never "exists"
        return False


def _previous_released_at(version: str) -> str | None:
    """The date already on record for this version, so a re-sync does not move it."""
    try:
        prev = json.loads(LATEST_JSON_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if prev.get("version") != version:
        return None
    value = prev.get("released_at")
    return value[:10] if isinstance(value, str) and value else None


def build_manifest(
    version: str,
    *,
    released_at: str | None = None,
    verify=_asset_exists,
) -> dict:
    """The published release manifest.

    `pypi` always resolves — publishing to PyPI is the step no release path skips.
    `download_url` is a GitHub Release asset on navig-run/core, which exists only once
    that release has been cut; a hand release (org Actions billing-blocked, tag workflow
    never fires) has NOT cut one, and the template URL 404'd on 3.25.0 from a public
    manifest for two weeks. So it is VERIFIED (HEAD) and written as null when absent —
    the workflow path, running after it created the release, gets the real URL.

    `released_at` is preserved for an already-recorded version (a re-sync is not a new
    release) and may be given explicitly to backfill; date only, the same shape in every
    writer.
    """
    url = DOWNLOAD_URL_TEMPLATE.format(version=version)
    exists = verify(url)
    if not exists:
        print(f"  [info] no GitHub Release asset at {url} - download_url written as null")
    when = released_at or _previous_released_at(version) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    manifest = {
        "version": version,
        "channel": "stable",
        "pypi": PYPI_URL_TEMPLATE.format(version=version),
        "download_url": url if exists else None,
        "changelog_url": CHANGELOG_URL,
        "released_at": when[:10],
    }
    if tuple(manifest) != MANIFEST_KEYS:  # the schema IS the contract; never ship a drift
        raise RuntimeError(f"manifest keys drifted from MANIFEST_KEYS: {tuple(manifest)}")
    return manifest


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
    print(f"  [ok] wrote {display}")


def maybe_sync_www(version: str, dry_run: bool) -> None:
    """Best-effort sync of the website-visible version - content/copy.ts's
    siteConfig.version + public/latest.json — via the canonical Node script. Targets
    the in-repo ``web/www`` (monorepo) or a legacy sibling ``navig-www``. Non-fatal:
    a missing web/www or Node is skipped with a warning, never failing the release.

    (The Node script reads the version from pyproject.toml — the same source of truth
    version_bump.py has already written — so the two never drift.)"""
    env_sync = os.environ.get("NAVIG_DEV_SYNC", "").strip()
    candidates = [
        REPO_ROOT.parent / "web" / "www",   # monorepo (labs)
        REPO_ROOT.parent / "navig-www",     # legacy polyrepo sibling (dead-path-ok)
    ]
    www_root = next((p for p in candidates if p.is_dir()), None)
    if www_root is None:
        if env_sync == "1":
            print("  [warn] NAVIG_DEV_SYNC=1 but no web/www (or navig-www) found - "
                  "site sync skipped", file=sys.stderr)
        return

    sync_script = www_root / "scripts" / "sync-site-version.mjs"
    if not sync_script.is_file():
        print(f"  [warn] {sync_script} missing - site version sync skipped", file=sys.stderr)
        return

    try:
        label = str(sync_script.relative_to(REPO_ROOT.parent))
    except ValueError:
        label = str(sync_script)
    if dry_run:
        print(f"  [dry-run] would run: node {label}  (syncs copy.ts + public/latest.json)")
        return

    node = shutil.which("node")
    if not node:
        print("  [warn] node not on PATH - website version sync skipped "
              "(run: npm --prefix web/www run sync:version)", file=sys.stderr)
        return
    try:
        # Lazy: release.yml runs this tool from a bare checkout where `navig` is not
        # installed, and only this www step needs it.
        from navig.core.proc_text import decode_console_result  # noqa: PLC0415

        result = decode_console_result(subprocess.run(
            [node, str(sync_script)], capture_output=True, timeout=120
        ))
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] website version sync skipped: {exc}", file=sys.stderr)
        return
    if result.returncode == 0:
        print("  [ok] synced website version (content/copy.ts + public/latest.json)")
    else:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        print(f"  [warn] website version sync failed: {detail}", file=sys.stderr)


def run(version: str | None = None, dry_run: bool = False, released_at: str | None = None) -> str:
    resolved = version or read_pyproject_version()
    manifest = build_manifest(resolved, released_at=released_at)

    print(f"Syncing version {resolved} to manifests:")
    write_json(LATEST_JSON_PATH, manifest, dry_run)
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
    parser.add_argument(
        "--released-at",
        default=None,
        metavar="YYYY-MM-DD",
        help="Release date to record (default: the date already on record for this "
             "version, else today)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        resolved = run(version=args.version, dry_run=args.dry_run, released_at=args.released_at)
        if args.dry_run:
            print(f"\nDry-run complete. Version: {resolved} (no files changed)")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
