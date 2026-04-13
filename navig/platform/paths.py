# NAVIG Platform Paths — Centralized cross-platform path resolution
"""
Single source of truth for all OS-specific paths used by NAVIG.

Usage::

    from navig.platform import paths

    config_dir = paths.config_dir()
    data_dir   = paths.data_dir()
    log_dir    = paths.log_dir()

    # Full diagnostic bundle
    info = paths.platform_info()

All other modules must import from here rather than doing ad-hoc
``sys.platform == 'win32'`` checks scattered across the codebase.

Performance note:
    OS detection uses ``sys.platform`` (a string constant set at interpreter
    startup, cost ≈ 0 ns) rather than ``platform.system()`` which triggers a
    WMI query on Windows Python 3.12+ that can block for 73 ms to infinity.
    The ``platform`` stdlib module is still imported for non-hot-path uses
    (``platform_info``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# ── OS Detection (module-level constant after first call) ────────────────────

_DETECTED_OS: str | None = None


def current_os() -> str:
    """Return the canonical OS name: ``'windows'``, ``'linux'``, ``'macos'``, or ``'wsl'``.

    The result is computed once and cached for the process lifetime.
    """
    global _DETECTED_OS
    if _DETECTED_OS is not None:
        return _DETECTED_OS

    p = sys.platform
    if p == "win32":
        _DETECTED_OS = "windows"
    elif p == "darwin":
        _DETECTED_OS = "macos"
    elif p == "linux":
        # WSL detection via /proc/version — fast file read, no WMI, no subprocess.
        try:
            proc_version = Path("/proc/version").read_text(encoding="utf-8", errors="replace")
            _DETECTED_OS = "wsl" if "microsoft" in proc_version.lower() else "linux"
        except (FileNotFoundError, PermissionError):
            _DETECTED_OS = "linux"
    else:
        # FreeBSD, OpenBSD, Cygwin, etc. — treat as Linux-like.
        _DETECTED_OS = "linux"

    return _DETECTED_OS


def is_windows() -> bool:
    return current_os() == "windows"


def is_linux() -> bool:
    return current_os() in ("linux", "wsl")


def is_macos() -> bool:
    return current_os() == "macos"


def is_wsl() -> bool:
    return current_os() == "wsl"


def is_unix() -> bool:
    return current_os() in ("linux", "macos", "wsl")


# ── Path Resolution ──────────────────────────────────────────────────────────


def home_dir() -> Path:
    """User home directory."""
    return Path.home()


def config_dir() -> Path:
    """NAVIG configuration directory.

    Respects the ``NAVIG_CONFIG_DIR`` environment variable.

    Defaults:
        - Windows: ``%USERPROFILE%\\.navig``
        - Linux / macOS / WSL: ``~/.navig``
        - System service: ``/etc/navig``
    """
    env = os.environ.get("NAVIG_CONFIG_DIR")
    if env:
        return Path(env)
    if _is_system_service():
        return Path("/etc/navig")
    return home_dir() / ".navig"


def data_dir() -> Path:
    """NAVIG state/data directory (databases, sessions).

    Respects ``NAVIG_DATA_DIR``.

    Defaults:
        - User: ``~/.navig/data``
        - System service: ``/var/lib/navig``
    """
    env = os.environ.get("NAVIG_DATA_DIR")
    if env:
        return Path(env)
    if _is_system_service():
        return Path("/var/lib/navig")
    return config_dir() / "data"


def log_dir() -> Path:
    """NAVIG log directory, following OS-idiomatic conventions.

    Respects ``NAVIG_LOG_DIR``.

    Defaults:
        - Windows:       ``%LOCALAPPDATA%\\navig\\logs``
        - macOS:         ``~/Library/Logs/navig``
        - Linux / WSL:   ``$XDG_STATE_HOME/navig/logs`` → ``~/.local/state/navig/logs``
        - System service: ``/var/log/navig``
    """
    env = os.environ.get("NAVIG_LOG_DIR")
    if env:
        return Path(env)

    if _is_system_service():
        return Path("/var/log/navig")

    os_name = current_os()

    if os_name == "windows":
        local_appdata = os.environ.get("LOCALAPPDATA")
        return Path(local_appdata) / "navig" / "logs" if local_appdata else config_dir() / "logs"

    if os_name == "macos":
        return home_dir() / "Library" / "Logs" / "navig"

    # Linux / WSL — XDG_STATE_HOME (systemd convention for mutable state logs)
    xdg_state = os.environ.get("XDG_STATE_HOME")
    return (
        Path(xdg_state) / "navig" / "logs"
        if xdg_state
        else home_dir() / ".local" / "state" / "navig" / "logs"
    )


def blackbox_dir() -> Path:
    """Telemetry / crash-report blackbox directory."""
    return data_dir() / "blackbox"


def cache_dir() -> Path:
    """NAVIG cache directory.

    Respects ``NAVIG_CACHE_DIR``.

    Defaults:
        - Windows:       ``%LOCALAPPDATA%\\navig\\cache``
        - macOS:         ``~/Library/Caches/navig``
        - Linux / WSL:   ``$XDG_CACHE_HOME/navig`` → ``~/.cache/navig``
        - System service: ``/var/cache/navig``
    """
    env = os.environ.get("NAVIG_CACHE_DIR")
    if env:
        return Path(env)

    if _is_system_service():
        return Path("/var/cache/navig")

    os_name = current_os()

    if os_name == "windows":
        local_appdata = os.environ.get("LOCALAPPDATA")
        return Path(local_appdata) / "navig" / "cache" if local_appdata else config_dir() / "cache"

    if os_name == "macos":
        return home_dir() / "Library" / "Caches" / "navig"

    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    return Path(xdg_cache) / "navig" if xdg_cache else home_dir() / ".cache" / "navig"


def workspace_dir() -> Path:
    """NAVIG workspace directory (identity, soul, agents, etc.)."""
    return config_dir() / "workspace"


def debug_log_path() -> Path:
    """Canonical path to the debug log file."""
    return log_dir() / "debug.log"


def global_config_path() -> Path:
    """Path to the main global config file.

    New layout:    ``config/config.yaml`` inside :func:`config_dir`.
    Legacy layout: ``config.yaml`` directly inside :func:`config_dir`.
    """
    new_path = config_dir() / "config" / "config.yaml"
    legacy = config_dir() / "config.yaml"
    return legacy if not new_path.exists() and legacy.exists() else new_path


def msg_trace_path() -> Path:
    """Path to the Telegram message trace log (stable legacy location)."""
    return config_dir() / "msg_trace.jsonl"


def genesis_json_path() -> Path:
    """Path to ``genesis.json`` with legacy fallback."""
    new_path = config_dir() / "state" / "genesis.json"
    legacy = config_dir() / "genesis.json"
    return legacy if not new_path.exists() and legacy.exists() else new_path


def entity_json_path() -> Path:
    """Path to ``entity.json`` with legacy fallback."""
    new_path = config_dir() / "state" / "entity.json"
    legacy = config_dir() / "entity.json"
    return legacy if not new_path.exists() and legacy.exists() else new_path


def onboarding_json_path() -> Path:
    """Path to ``onboarding.json`` with legacy fallback."""
    new_path = config_dir() / "state" / "onboarding.json"
    legacy = config_dir() / "onboarding.json"
    return legacy if not new_path.exists() and legacy.exists() else new_path


def builtin_store_dir() -> Path:
    """Built-in content store bundled with NAVIG (``store/`` in project root)."""
    return Path(__file__).resolve().parents[2] / "store"


def builtin_packages_dir() -> Path:
    """Built-in packages bundled with NAVIG (``packages/`` in project root)."""
    return Path(__file__).resolve().parents[2] / "packages"


def vault_dir() -> Path:
    """Encrypted vault storage directory."""
    return config_dir() / "vault"


def store_dir() -> Path:
    """User content store, with env override and legacy fallback."""
    env = os.environ.get("NAVIG_STORE_DIR")
    if env:
        return Path(env)
    new_path = config_dir() / "data" / "store"
    legacy = config_dir() / "store"
    return legacy if not new_path.exists() and legacy.exists() else new_path


def packages_dir() -> Path:
    """Installed pack directory, with env override and legacy fallback."""
    env = os.environ.get("NAVIG_PACKAGES_DIR")
    if env:
        return Path(env)
    new_path = config_dir() / "packs"
    legacy = config_dir() / "packages"
    return legacy if not new_path.exists() and legacy.exists() else new_path


def audio_configs_dir() -> Path:
    """Audio provider configs directory with legacy fallback."""
    new_path = config_dir() / "config" / "audio"
    legacy = config_dir() / "audio_configs"
    return legacy if not new_path.exists() and legacy.exists() else new_path


def ssh_key_dir() -> Path:
    """SSH keys directory (``~/.ssh``)."""
    return home_dir() / ".ssh"


def temp_dir() -> Path:
    """Temporary directory for NAVIG operations."""
    import tempfile
    return Path(tempfile.gettempdir()) / "navig"


def stack_dir() -> Path:
    """Infrastructure stack directory (docker-compose, .env, etc.).

    Respects ``NAVIG_STACK_DIR``.

    Defaults:
        - System service: ``/opt/navig``
        - Local server (``/opt/navig`` exists): ``/opt/navig``
        - User: ``~/.navig/stack``
    """
    env = os.environ.get("NAVIG_STACK_DIR")
    if env:
        return Path(env)
    if _is_system_service():
        return Path("/opt/navig")
    opt_navig = Path("/opt/navig")
    if is_unix() and opt_navig.is_dir():
        return opt_navig
    return config_dir() / "stack"


# ── App-root discovery ────────────────────────────────────────────────────────


def find_app_root(verbose: bool = False) -> Path | None:
    """Walk upward from CWD to find a directory that contains ``.navig/``.

    Returns the directory containing ``.navig/``, or ``None`` if not found.
    """
    current = Path.cwd()
    while True:
        navig_dir = current / ".navig"
        try:
            if navig_dir.is_dir():
                if is_directory_accessible(navig_dir):
                    return current
                elif verbose:
                    _warn(f"Found .navig at {navig_dir} but cannot access it (permission denied)")
        except (PermissionError, OSError) as exc:
            if verbose:
                _warn(f"Cannot check {navig_dir}: {exc}")

        parent = current.parent
        if parent == current:
            return None
        current = parent


def _warn(msg: str) -> None:
    """Emit a warning via console_helper, falling back to stderr."""
    try:
        from navig import console_helper as ch
        ch.warning(msg)
    except ImportError:
        sys.stderr.write(f"WARNING: {msg}\n")


def is_directory_accessible(directory: Path) -> bool:
    """Return ``True`` if *directory* exists and can be listed.

    Creates the directory if it does not yet exist (fresh installs).
    """
    try:
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
        if directory.is_dir():
            # A single iterdir() is cheaper than list(iterdir()) for large dirs.
            next(directory.iterdir(), None)
            return True
    except (PermissionError, OSError) as exc:
        import logging as _logging
        _logging.getLogger(__name__).debug(
            "is_directory_accessible(%s) failed: %s", directory, exc
        )
    return False


# ── Shell helpers ─────────────────────────────────────────────────────────────


def shell_name() -> str:
    """Detect the user's interactive shell name."""
    if is_windows():
        return "powershell"

    shell = os.environ.get("SHELL", "")
    for name in ("zsh", "fish", "bash"):
        if name in shell:
            return name
    return "sh"


def shell_rc_path() -> Path | None:
    """Return the shell RC file path for PATH modifications, or ``None`` on Windows."""
    if is_windows():
        return None

    home = home_dir()
    return {
        "zsh": home / ".zshrc",
        "bash": home / ".bashrc",
        "fish": home / ".config" / "fish" / "config.fish",
    }.get(shell_name(), home / ".profile")


# ── System-service detection ──────────────────────────────────────────────────


def _is_system_service() -> bool:
    """Return ``True`` when NAVIG is running as a system (root) service."""
    if os.environ.get("NAVIG_SYSTEM_SERVICE") == "1":
        return True

    if is_unix():
        try:
            import pwd
            if pwd.getpwuid(os.getuid()).pw_name == "navig":
                return True
        except (ImportError, KeyError):
            pass

        # INVOCATION_ID is set by systemd for all services including user-level
        # ones, so we additionally check uid==0 to avoid redirecting user-level
        # daemon paths to /var/log/navig.
        if os.environ.get("INVOCATION_ID"):
            try:
                if os.getuid() == 0:
                    return True
            except AttributeError:
                pass  # Windows stub — never reached because of is_unix() guard

    return False


# ── Platform info bundle ──────────────────────────────────────────────────────


def platform_info() -> dict[str, Any]:
    """Return a full platform diagnostic bundle.

    Suitable for ``navig status``, crash reports, and support tickets.
    """
    import platform

    info: dict[str, Any] = {
        "os": current_os(),
        "os_version": platform.version(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "hostname": platform.node(),
        "is_system_service": _is_system_service(),
        "paths": {
            "config": str(config_dir()),
            "data": str(data_dir()),
            "logs": str(log_dir()),
            "cache": str(cache_dir()),
            "workspace": str(workspace_dir()),
            "stack": str(stack_dir()),
        },
        "shell": shell_name(),
    }

    if is_linux() or is_wsl():
        try:
            with open("/etc/os-release", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("PRETTY_NAME="):
                        info["distro"] = line.split("=", 1)[1].strip().strip('"')
                        break
        except FileNotFoundError:
            pass

    return info


# ── Directory bootstrap ───────────────────────────────────────────────────────


def ensure_dirs() -> None:
    """Create all standard NAVIG directories if they do not already exist."""
    for d in (config_dir(), data_dir(), log_dir(), cache_dir(), workspace_dir()):
        d.mkdir(parents=True, exist_ok=True)


# ── Dependency checks ─────────────────────────────────────────────────────────


def check_docker() -> dict[str, Any]:
    """Check Docker availability and return a status dict.

    Returns a dict with keys:
        ``available`` (bool), ``version`` (str | None),
        ``compose`` (bool), ``compose_version`` (str | None).
    """
    import subprocess as _sp

    result: dict[str, Any] = {
        "available": False,
        "version": None,
        "compose": False,
        "compose_version": None,
    }

    try:
        proc = _sp.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            result["available"] = True
            ver_text = proc.stdout.strip()
            if "version" in ver_text.lower():
                result["version"] = (
                    ver_text.split("version")[-1].split(",")[0].strip()
                )
    except (FileNotFoundError, _sp.TimeoutExpired):
        pass

    if result["available"]:
        try:
            proc = _sp.run(
                ["docker", "compose", "version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                result["compose"] = True
                result["compose_version"] = proc.stdout.strip()
        except (FileNotFoundError, _sp.TimeoutExpired):
            pass

    return result
