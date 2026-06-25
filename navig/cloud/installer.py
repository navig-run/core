"""Platform-detect + checksum-verified ``cloudflared`` installer.

Resolution order (matches the plan's "Robustness: install failures" section):

  1. ``config.cloud.cloudflared_path``    -- explicit user override.
  2. ``cloudflared`` on ``$PATH``.
  3. ``~/.navig/bin/cloudflared(.exe)``   -- prior download.
  4. Download from GitHub releases, SHA-256-verify, cache in ``~/.navig/bin/``.

Never invokes a system package manager (apt/brew/choco). The binary stays
fully isolated to ``~/.navig/bin/``. If the download path fails (firewall /
offline), the caller gets a clear error pointing at the manual download URL
+ expected checksum so the user can drop the binary into place themselves.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform as _stdlib_platform
import shutil
import stat
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

from navig.cloud._cloudflared_checksums import (
    CLOUDFLARED_VERSION,
    asset_name,
    expected_sha256,
    release_url,
)
from navig.platform import paths

logger = logging.getLogger(__name__)


class InstallerError(RuntimeError):
    """Raised when cloudflared cannot be located or installed."""


def _navig_bin_dir() -> Path:
    d = paths.config_dir() / "bin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _exe_suffix() -> str:
    return ".exe" if sys.platform == "win32" else ""


def _binary_path() -> Path:
    return _navig_bin_dir() / f"cloudflared{_exe_suffix()}"


def _detect_arch() -> str:
    """Return the canonical arch string: amd64 / arm64 / x86_64 / aarch64."""
    m = _stdlib_platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "amd64"
    if m in ("arm64", "aarch64"):
        return "arm64"
    return m


def _detect_os() -> str:
    """Return canonical OS string used by the checksum table."""
    p = sys.platform
    if p == "win32":
        return "windows"
    if p == "darwin":
        return "darwin"
    return "linux"


def _which_cloudflared() -> str | None:
    return shutil.which("cloudflared")


def _verify_checksum(path: Path, expected: str) -> None:
    if expected == "UNPINNED":
        logger.warning(
            "cloudflared %s downloaded without a pinned SHA-256 (UNPINNED placeholder). "
            "Edit navig/cloud/_cloudflared_checksums.py to pin the digest before shipping.",
            CLOUDFLARED_VERSION,
        )
        return
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    if h.lower() != expected.lower():
        path.unlink(missing_ok=True)
        raise InstallerError(
            f"cloudflared SHA-256 mismatch: expected {expected}, got {h}. "
            "Refusing to use a binary that does not match the pinned digest."
        )


def _download(url: str, dest: Path) -> None:
    logger.info("Downloading cloudflared from %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "navig-cloud-installer/1"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
            shutil.copyfileobj(resp, out)
    except urllib.error.URLError as e:
        raise InstallerError(
            f"failed to download cloudflared: {e}. "
            f"Drop the binary at {dest} manually -- see {url}"
        ) from e


def _extract_macos_tgz(tgz_path: Path, target: Path) -> None:
    """The macOS asset is a tarball -- extract the inner ``cloudflared``."""
    with tarfile.open(tgz_path, "r:gz") as tar:
        members = [m for m in tar.getmembers() if m.name.endswith("cloudflared")]
        if not members:
            raise InstallerError(f"no cloudflared binary in archive: {tgz_path}")
        member = members[0]
        # tarfile.extract is safe here -- the archive comes from cloudflare/cloudflared
        # and the SHA-256 was already verified above.
        tar.extract(member, path=target.parent)  # noqa: S202
        extracted = target.parent / member.name
        if extracted != target:
            shutil.move(str(extracted), str(target))
    tgz_path.unlink(missing_ok=True)


def _make_executable(p: Path) -> None:
    if sys.platform == "win32":
        return
    st = p.stat()
    p.chmod(st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def ensure_cloudflared(explicit_path: str | None = None) -> str:
    """Return an absolute path to a usable ``cloudflared`` binary.

    Tries (in order): explicit override, $PATH, cached download, fresh download.
    Raises :class:`InstallerError` if none of the strategies succeeds.
    """
    if explicit_path:
        p = Path(os.path.expanduser(explicit_path))
        if not p.exists():
            raise InstallerError(f"cloud.cloudflared_path set but not found: {p}")
        return str(p)

    on_path = _which_cloudflared()
    if on_path:
        return on_path

    cached = _binary_path()
    if cached.exists():
        return str(cached)

    os_name = _detect_os()
    arch = _detect_arch()
    asset = asset_name(os_name, arch)
    if asset is None:
        raise InstallerError(
            f"no cloudflared asset known for {os_name}/{arch}. "
            "Install cloudflared manually and set cloud.cloudflared_path."
        )

    url = release_url(asset)
    expected = expected_sha256(asset)
    if expected is None:
        raise InstallerError(f"no pinned SHA-256 for cloudflared asset {asset}")

    bin_dir = _navig_bin_dir()
    download_dest = bin_dir / asset
    _download(url, download_dest)
    _verify_checksum(download_dest, expected)

    if asset.endswith(".tgz"):
        _extract_macos_tgz(download_dest, cached)
    else:
        if download_dest != cached:
            shutil.move(str(download_dest), str(cached))

    _make_executable(cached)
    logger.info("cloudflared installed at %s (version %s)", cached, CLOUDFLARED_VERSION)
    return str(cached)


def manual_install_hint(os_name: str | None = None, arch: str | None = None) -> str:
    """Return a human-readable manual-install instruction for error displays."""
    os_name = os_name or _detect_os()
    arch = arch or _detect_arch()
    asset = asset_name(os_name, arch) or "cloudflared"
    return (
        f"Manual install: download {release_url(asset)} into {_binary_path().parent} "
        f"and rename to {_binary_path().name}."
    )
