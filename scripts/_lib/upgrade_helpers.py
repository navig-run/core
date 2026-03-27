"""
scripts/_lib/upgrade_helpers.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Shared download / extract utilities for NAVIG tool upgrade scripts.

Import from upgrade_tools.py and upgrade_usb_tools.py:

    from _lib.upgrade_helpers import (
        TEMP, progress, ok, warn, skip,
        download, extract_single, extract_glob, extract_all_into,
        cleanup,
    )
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlretrieve

# ── Shared temp directory ─────────────────────────────────────────────────────
TEMP = Path(tempfile.mkdtemp(prefix="navig_upgrade_"))


# ── Output helpers ────────────────────────────────────────────────────────────


def progress(label: str, msg: str) -> None:
    print(f"  [{label}] {msg}")


def ok(label: str, version_and_path: str = "") -> None:
    msg = f"  ✓ {label}"
    if version_and_path:
        msg += f"  {version_and_path}"
    print(msg)


def warn(label: str, msg: str) -> None:
    print(f"  ✗ [{label}] {msg}", file=sys.stderr)


def skip(label: str, reason: str) -> None:
    print(f"  → {label}: {reason}")


# ── Download ──────────────────────────────────────────────────────────────────


def download(label: str, url: str, dest: Path | str) -> Path | None:
    """
    Download *url* to *dest*.

    *dest* may be an absolute Path (used as the output file directly)
    or a filename string (resolved relative to TEMP).
    """
    if isinstance(dest, str):
        dest_path: Path = TEMP / dest
    else:
        dest_path = dest if dest.is_absolute() else TEMP / dest.name

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = TEMP / dest_path.name

    progress(label, f"Downloading {url.split('/')[-1]} …")
    try:
        urlretrieve(url, tmp)
        size_kb = int(tmp.stat().st_size / 1024)
        progress(label, f"Downloaded {size_kb} KB")
        return tmp
    except URLError as exc:
        warn(label, f"Download failed: {exc}")
        return None


# ── Extract ───────────────────────────────────────────────────────────────────


def extract_single(label: str, archive: Path, match: str, target: Path) -> bool:
    """Extract the first entry whose basename matches *match* into *target*."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        hits = [n for n in z.namelist() if Path(n).name.lower() == match.lower()]
        if not hits:
            warn(
                label,
                f"'{match}' not found in zip (contents: {[Path(n).name for n in z.namelist()[:12]]})",
            )
            return False
        src = hits[0]
        progress(label, f"Extracting {src} → {target}")
        with z.open(src) as fin, open(target, "wb") as fout:
            shutil.copyfileobj(fin, fout)
    return True


def extract_glob(label: str, archive: Path, target_dir: Path, exts: list[str]) -> bool:
    """Extract all entries whose extension is in *exts* into *target_dir*."""
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        extracted: list[str] = []
        for name in z.namelist():
            if any(name.lower().endswith(ext) for ext in exts):
                fname = Path(name).name
                dest = target_dir / fname
                progress(label, f"Extracting {fname}")
                with z.open(name) as fin, open(dest, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
                extracted.append(fname)
        return bool(extracted)


def extract_all_into(
    label: str,
    archive: Path,
    target_dir: Path,
    strip_components: int = 1,
) -> bool:
    """
    Extract *archive* into *target_dir*, stripping *strip_components*
    leading path components from each entry.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        count = 0
        for entry in z.infolist():
            parts = Path(entry.filename).parts
            if len(parts) <= strip_components:
                continue
            rel = Path(*parts[strip_components:])
            dest = target_dir / rel
            if entry.filename.endswith("/"):
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(entry) as fin, open(dest, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
                count += 1
        if count:
            progress(label, f"Extracted {count} files → {target_dir}")
        return count > 0


# ── Cleanup ───────────────────────────────────────────────────────────────────


def cleanup() -> None:
    """Remove the shared TEMP directory."""
    shutil.rmtree(TEMP, ignore_errors=True)
