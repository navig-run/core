"""Opt-in "max stealth" engine — a hardened Chromium build driven over CDP.

The engine is patched at the **C++ layer** so ``navigator.*``, canvas/WebGL, TLS JA3/JA4,
GPU tables and fonts are coherent end-to-end — the "seam" that trips JS-shim stealth
(values disagreeing with the network) doesn't exist. It speaks standard CDP, so NAVIG
drives it exactly like any other Chrome: launch with a debug port, then ``connect_over_cdp``.

Posture: the engine is **opt-in, downloaded on demand, checksum-pinned**, never bundled in
the wheel — the default engine stays technique-level (Patchright + fingerprint). Selected
via ``router.get_browser(engine="hardened")`` or ``browser.engine = hardened``.

Config::

    browser:
      hardened:
        path: "C:/.../hardened/chrome.exe"   # explicit binary, OR
        url:  "https://…/hardened-win64.zip"  # download-on-demand source
        sha256: "…"                           # REQUIRED to accept a download

Back-compat: the legacy ``browser.clearcote.*`` config block, the legacy
``engines/clearcote`` download dir, and ``engine="clearcote"`` are still honored so
existing setups keep working after the 3.24 rename.
"""

from __future__ import annotations

import hashlib
import socket
import subprocess
import sys
import time
from pathlib import Path

from navig.browser.cdp_bridge import CDPBridge
from navig.browser.targets import CHROMIUM_QUIET_ARGS
from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

__all__ = ["HardenedController", "HardenedEngineUnavailable", "hardened_binary_path",
           "is_available", "ensure_hardened"]


class HardenedEngineUnavailable(RuntimeError):
    """The hardened engine binary isn't present (and couldn't be provisioned)."""


def _managed_dir() -> Path:
    from navig.platform.paths import config_dir  # noqa: PLC0415

    return config_dir() / "browser" / "engines" / "hardened"


def _legacy_managed_dir() -> Path:
    """Pre-3.24 download dir — still checked so an existing binary keeps resolving."""
    from navig.platform.paths import config_dir  # noqa: PLC0415

    return config_dir() / "browser" / "engines" / "clearcote"


def _binary_name() -> str:
    return "chrome.exe" if sys.platform == "win32" else "chrome"


def _hardened_config() -> dict:
    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        browser = get_config_manager().global_config.get("browser", {}) or {}
        # Primary key `hardened`; the legacy `clearcote` block is still honored.
        return (browser.get("hardened") or browser.get("clearcote") or {})
    except Exception:  # noqa: BLE001
        return {}


def hardened_binary_path() -> Path | None:
    """Resolve the hardened binary: explicit config path → managed dir → legacy dir → None."""
    cfg = _hardened_config()
    explicit = (cfg.get("path") or "").strip()
    if explicit and Path(explicit).exists():
        return Path(explicit)
    name = _binary_name()
    managed = _managed_dir() / name
    if managed.exists():
        return managed
    legacy = _legacy_managed_dir() / name  # pre-3.24 downloads
    return legacy if legacy.exists() else None


def is_available() -> bool:
    return hardened_binary_path() is not None


def _verify_checksum(path: Path, expected_sha256: str) -> bool:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().lower() == expected_sha256.strip().lower()


def ensure_hardened() -> Path:
    """Return the hardened binary path, downloading it on demand (checksum-pinned).

    Raises ``HardenedEngineUnavailable`` if no binary is present and no verifiable download
    source (``url`` + ``sha256``) is configured. A download is accepted ONLY if its
    SHA-256 matches the pinned value — never an unverified 3rd-party engine.
    """
    existing = hardened_binary_path()
    if existing is not None:
        return existing

    cfg = _hardened_config()
    url = (cfg.get("url") or "").strip()
    sha = (cfg.get("sha256") or "").strip()
    if not url or not sha:
        raise HardenedEngineUnavailable(
            "hardened engine is not installed. Set browser.hardened.path to an existing binary, "
            "or browser.hardened.url + browser.hardened.sha256 to provision it on demand "
            "(checksum-pinned). See docs — the engine is downloaded, never bundled."
        )

    dest_dir = _managed_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive = dest_dir / "download.bin"
    logger.info("[hardened] downloading engine (checksum-pinned) …")
    try:
        import urllib.request  # noqa: PLC0415

        urllib.request.urlretrieve(url, archive)  # noqa: S310 — url is operator-configured
    except Exception as exc:  # noqa: BLE001
        raise HardenedEngineUnavailable(f"hardened engine download failed: {exc}") from exc

    if not _verify_checksum(archive, sha):
        archive.unlink(missing_ok=True)
        raise HardenedEngineUnavailable(
            "hardened engine download REJECTED — SHA-256 mismatch (refusing to run an "
            "unverified browser engine)."
        )
    # Extraction layout is provider-specific; unpack then re-resolve.
    _extract_archive(archive, dest_dir)
    archive.unlink(missing_ok=True)
    resolved = hardened_binary_path()
    if resolved is None:
        raise HardenedEngineUnavailable(
            "hardened engine archive verified but no binary found after extraction — check the "
            "archive layout / set browser.hardened.path explicitly."
        )
    return resolved


def _extract_archive(archive: Path, dest_dir: Path) -> None:
    import shutil  # noqa: PLC0415

    try:
        shutil.unpack_archive(str(archive), str(dest_dir))
    except Exception as exc:  # noqa: BLE001 — not a recognised archive → leave as-is
        logger.debug("[hardened] unpack_archive skipped (%s)", exc)


def _wait_for_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


class HardenedController(CDPBridge):
    """Launch the hardened engine and drive it over CDP (owns the process it starts)."""

    def __init__(self, *, port: int = 9333, user_data_dir: str | None = None,
                 headless: bool = True, proxy: str | None = None,
                 webrtc_protection: bool = True, binary: str | None = None):
        super().__init__(debug_port=port)
        self._headless = headless
        self._proxy = proxy
        self._webrtc = webrtc_protection
        self._binary = binary
        self._user_data_dir = user_data_dir or str(_managed_dir() / "userdata")
        self._proc: subprocess.Popen | None = None

    def _connection_hint(self) -> str:
        return ("The hardened engine failed to expose its CDP port — check the binary "
                "launched (see logs) and that no other process holds the port.")

    def _build_args(self, binary: Path) -> list[str]:
        args = [
            str(binary),
            f"--remote-debugging-port={self.debug_port}",
            f"--user-data-dir={self._user_data_dir}",
            *CHROMIUM_QUIET_ARGS,
        ]
        if self._headless:
            args.append("--headless=new")
        if self._proxy:
            from navig.browser.proxy import ProxySpec  # noqa: PLC0415

            args.append(f"--proxy-server={ProxySpec.from_url(self._proxy).server}")
        if self._webrtc:
            from navig.browser.fingerprint import webrtc_launch_args  # noqa: PLC0415

            args += webrtc_launch_args()
        return args

    async def start(self):
        binary = ensure_hardened()  # raises HardenedEngineUnavailable with guidance
        Path(self._user_data_dir).mkdir(parents=True, exist_ok=True)
        args = self._build_args(binary)
        logger.info("[hardened] launching engine on port %d …", self.debug_port)
        self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not _wait_for_port(self.debug_port):
            self._terminate_proc()
            raise HardenedEngineUnavailable(
                f"hardened engine did not open CDP port {self.debug_port} within timeout."
            )
        await super().start()  # attach over CDP to the process we just launched

    async def stop(self):
        # Disconnect the CDP session, THEN kill the process we own (attach mode leaves it
        # running; here we started it, so we must clean it up).
        await super().stop()
        self._terminate_proc()

    def _terminate_proc(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    self._proc.kill()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[hardened] process teardown: %s", exc)
            finally:
                self._proc = None
