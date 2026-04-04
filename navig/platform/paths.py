# NAVIG Platform Paths — Centralized cross-platform path resolution
"""
Single source of truth for all OS-specific paths used by NAVIG.

Usage:
    from navig.platform import paths
    config_dir = paths.config_dir()
    data_dir = paths.data_dir()
    log_dir = paths.log_dir()

    # Or get everything at once
    info = paths.platform_info()

All other modules should import paths from here instead of
doing ad-hoc `sys.platform == 'win32'` checks.

Performance note:
    OS detection uses `sys.platform` (set at interpreter init, 0 ns)
    instead of `platform.system()` which triggers a WMI query on
    Windows Python 3.12+ that can hang for 73 ms to infinity.
    `platform` is still imported for non-hot-path uses (platform_info).
"""

import os
import platform
import sys
from pathlib import Path
from typing import Any

# ── OS Detection (cached) ────────────────────────────────────

_DETECTED_OS: str | None = None


def current_os() -> str:
    """
    Detect current OS. Returns 'windows', 'linux', 'macos', or 'wsl'.
    Cached after first call.

    Uses ``sys.platform`` (a string constant, set at interpreter startup)
    rather than ``platform.system()`` which triggers a WMI query on
    Windows Python 3.12+ that can block for 73 ms to infinity.
    """
    global _DETECTED_OS
    if _DETECTED_OS is not None:
        return _DETECTED_OS

    # sys.platform is a constant — no OS calls, no WMI, no subprocess.
    # Values: 'win32' (all Windows), 'darwin' (macOS), 'linux' (Linux/WSL)
    _p = sys.platform
    if _p == "win32":
        _DETECTED_OS = "windows"
    elif _p == "darwin":
        _DETECTED_OS = "macos"
    elif _p == "linux":
        # Check for WSL via /proc/version (fast file read, no WMI)
        try:
            with open("/proc/version") as f:
                if "microsoft" in f.read().lower():
                    _DETECTED_OS = "wsl"
                else:
                    _DETECTED_OS = "linux"
        except (FileNotFoundError, PermissionError):
            _DETECTED_OS = "linux"
    else:
        # FreeBSD, OpenBSD, Cygwin etc. — treat as Linux-like
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


# ── Path Resolution ──────────────────────────────────────────


def home_dir() -> Path:
    """User home directory."""
    return Path.home()


def config_dir() -> Path:
    """
    NAVIG configuration directory.
    Respects NAVIG_CONFIG_DIR env var.

    Defaults:
        Windows:  %USERPROFILE%\\.navig
        Linux:    ~/.navig
        macOS:    ~/.navig
        Server:   /etc/navig (if running as system service)
    """
    env = os.environ.get("NAVIG_CONFIG_DIR")
    if env:
        return Path(env)

    # System service mode
    if _is_system_service():
        return Path("/etc/navig")

    return home_dir() / ".navig"


def data_dir() -> Path:
    """
    NAVIG data directory (databases, state, sessions).
    Respects NAVIG_DATA_DIR env var.

    Defaults:
        Windows:  %USERPROFILE%\\.navig\\data
        Linux:    ~/.navig/data  (user) or /var/lib/navig (system)
        macOS:    ~/.navig/data
    """
    env = os.environ.get("NAVIG_DATA_DIR")
    if env:
        return Path(env)

    if _is_system_service():
        return Path("/var/lib/navig")

    return config_dir() / "data"


def log_dir() -> Path:
    """
    NAVIG log directory — stored in the OS-idiomatic user-local data area.
    Respects NAVIG_LOG_DIR env var.

    Defaults:
        Windows:  %LOCALAPPDATA%\\navig\\logs
        macOS:    ~/Library/Logs/navig
        Linux:    $XDG_STATE_HOME/navig/logs  (~/.local/state/navig/logs)
        System:   /var/log/navig
    """
    env = os.environ.get("NAVIG_LOG_DIR")
    if env:
        return Path(env)

    if _is_system_service():
        return Path("/var/log/navig")

    os_name = current_os()

    if os_name == "windows":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / "navig" / "logs"
        return config_dir() / "logs"

    if os_name == "macos":
        return home_dir() / "Library" / "Logs" / "navig"

    # Linux / WSL: follow XDG \u2014 logs live under XDG_STATE_HOME (systemd convention)
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "navig" / "logs"
    return home_dir() / ".local" / "state" / "navig" / "logs"


def blackbox_dir() -> Path:
    """
    NAVIG blackbox directory for telemetry events and crash reports.
    """
    return data_dir() / "blackbox"


def cache_dir() -> Path:
    """
    NAVIG cache directory.
    Respects NAVIG_CACHE_DIR env var.

    Defaults:
        Windows:  %LOCALAPPDATA%\\navig\\cache  (or ~/.navig/cache)
        Linux:    ~/.cache/navig  (user) or /var/cache/navig (system)
        macOS:    ~/Library/Caches/navig
    """
    env = os.environ.get("NAVIG_CACHE_DIR")
    if env:
        return Path(env)

    if _is_system_service():
        return Path("/var/cache/navig")

    os_name = current_os()

    if os_name == "windows":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / "navig" / "cache"
        return config_dir() / "cache"

    if os_name == "macos":
        return home_dir() / "Library" / "Caches" / "navig"

    # Linux / WSL: follow XDG
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache) / "navig"
    return home_dir() / ".cache" / "navig"


def workspace_dir() -> Path:
    """
    NAVIG workspace directory (identity files, soul, agents, etc.).
    Always under config_dir/workspace.
    """
    return config_dir() / "workspace"


def debug_log_path() -> Path:
    """Path to the debug log file (inside the platform log directory)."""
    return log_dir() / "debug.log"


def global_config_path() -> Path:
    """Path to the main global config file (config.yaml).

    New layout: config/config.yaml
    Legacy fallback: config.yaml at the root of the NAVIG directory.
    """
    new_path = config_dir() / "config" / "config.yaml"
    legacy = config_dir() / "config.yaml"
    if not new_path.exists() and legacy.exists():
        return legacy
    return new_path


def msg_trace_path() -> Path:
    """Path to Telegram message trace log with stable legacy location.

    The trace file has historically lived at ``~/.navig/msg_trace.jsonl``.
    Keep that path stable for compatibility with existing telemetry and tests.
    """
    return config_dir() / "msg_trace.jsonl"


def genesis_json_path() -> Path:
    """Path to genesis.json with legacy fallback."""
    new_path = config_dir() / "state" / "genesis.json"
    legacy = config_dir() / "genesis.json"
    if not new_path.exists() and legacy.exists():
        return legacy
    return new_path


def entity_json_path() -> Path:
    """Path to entity.json with legacy fallback."""
    new_path = config_dir() / "state" / "entity.json"
    legacy = config_dir() / "entity.json"
    if not new_path.exists() and legacy.exists():
        return legacy
    return new_path


def onboarding_json_path() -> Path:
    """Path to onboarding.json with legacy fallback."""
    new_path = config_dir() / "state" / "onboarding.json"
    legacy = config_dir() / "onboarding.json"
    if not new_path.exists() and legacy.exists():
        return legacy
    return new_path


def builtin_store_dir() -> Path:
    """Built-in content store bundled with NAVIG (store/ in the project root).

    Contains formations, skills, templates, and other content packages
    shipped with NAVIG. Use ``store_dir()`` for user-installed content.
    """
    return Path(__file__).resolve().parent.parent.parent / "store"


def builtin_packages_dir() -> Path:
    """Built-in packages bundled with NAVIG (packages/ in the project root)."""
    return Path(__file__).resolve().parent.parent.parent / "packages"


def vault_dir() -> Path:
    """Encrypted vault storage directory (~/.navig/vault)."""
    return config_dir() / "vault"


def store_dir() -> Path:
    """User content store location, with env override and legacy fallback."""
    env = os.environ.get("NAVIG_STORE_DIR")
    if env:
        return Path(env)

    new_path = config_dir() / "data" / "store"
    legacy = config_dir() / "store"
    if not new_path.exists() and legacy.exists():
        return legacy
    return new_path


def packages_dir() -> Path:
    """Installed pack directory, with env override and legacy fallback."""
    env = os.environ.get("NAVIG_PACKAGES_DIR")
    if env:
        return Path(env)

    new_path = config_dir() / "packs"
    legacy = config_dir() / "packages"
    if not new_path.exists() and legacy.exists():
        return legacy
    return new_path


def audio_configs_dir() -> Path:
    """Audio provider configs directory with legacy fallback."""
    new_path = config_dir() / "config" / "audio"
    legacy = config_dir() / "audio_configs"
    if not new_path.exists() and legacy.exists():
        return legacy
    return new_path


def ssh_key_dir() -> Path:
    """SSH keys directory."""
    return home_dir() / ".ssh"


def temp_dir() -> Path:
    """Temporary directory for NAVIG operations."""
    import tempfile

    return Path(tempfile.gettempdir()) / "navig"


def stack_dir() -> Path:
    """
    NAVIG infrastructure stack directory (docker-compose, .env, etc.).
    Respects NAVIG_STACK_DIR env var.

    Defaults:
        Server mode: /opt/navig
        User mode:   ~/.navig/stack
    """
    env = os.environ.get("NAVIG_STACK_DIR")
    if env:
        return Path(env)

    if _is_system_service():
        return Path("/opt/navig")

    # Check if /opt/navig exists and is usable (local server setup)
    opt_navig = Path("/opt/navig")
    if is_unix() and opt_navig.is_dir():
        return opt_navig

    return config_dir() / "stack"


# ── Shell helpers ─────────────────────────────────────────────


def shell_name() -> str:
    """Detect the user's shell."""
    if is_windows():
        return "powershell"

    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return "zsh"
    elif "fish" in shell:
        return "fish"
    elif "bash" in shell:
        return "bash"
    return "sh"


def shell_rc_path() -> Path | None:
    """Path to the shell's RC file for PATH modifications."""
    if is_windows():
        return None  # Windows uses registry/environment variables

    shell = shell_name()
    home = home_dir()

    rc_map = {
        "zsh": home / ".zshrc",
        "bash": home / ".bashrc",
        "fish": home / ".config" / "fish" / "config.fish",
    }
    return rc_map.get(shell, home / ".profile")


# ── System service detection ──────────────────────────────────


def _is_system_service() -> bool:
    """
    Check if NAVIG is running as a system service (not as a user CLI).
    Detects: systemd, running as 'navig' system user, or explicit env var.
    """
    # Explicit flag
    if os.environ.get("NAVIG_SYSTEM_SERVICE") == "1":
        return True

    # Running as dedicated system user
    if is_unix():
        try:
            import pwd

            user = pwd.getpwuid(os.getuid()).pw_name
            if user == "navig":
                return True
        except (ImportError, KeyError):
            pass  # optional platform info unavailable

    # Systemd detection — only a *system* service if running as root.
    # INVOCATION_ID is set by systemd for all services (including user services
    # like `User=void`), so we must also check uid to avoid treating user-level
    # daemons as system services and redirecting their paths to /var/log/navig.
    if os.environ.get("INVOCATION_ID") and is_unix():
        try:
            if os.getuid() == 0:
                return True
        except AttributeError:
            pass  # Windows — no getuid

    return False


# ── Platform info bundle ──────────────────────────────────────


def platform_info() -> dict[str, Any]:
    """
    Full platform information bundle.
    Useful for diagnostics, logging, and `navig status`.
    """
    info = {
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
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        info["distro"] = line.split("=", 1)[1].strip().strip('"')
                        break
        except FileNotFoundError:
            pass  # file already gone; expected

    return info


# ── Ensure directories exist ─────────────────────────────────


def ensure_dirs() -> None:
    """Create all NAVIG directories if they don't exist."""
    for d in [config_dir(), data_dir(), log_dir(), cache_dir(), workspace_dir()]:
        d.mkdir(parents=True, exist_ok=True)


# ── Dependency checks ────────────────────────────────────────


def check_docker() -> dict[str, Any]:
    """
    Check Docker availability and version.
    Returns dict with 'available', 'version', 'compose', 'compose_version'.
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
            ["docker", "--version"], capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0:
            result["available"] = True
            # "Docker version 29.2.1, build a5c7197"
            ver_text = proc.stdout.strip()
            if "version" in ver_text.lower():
                result["version"] = ver_text.split("version")[-1].split(",")[0].strip()
    except (FileNotFoundError, _sp.TimeoutExpired):
        pass  # tool absent or timed out; optional check

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
            pass  # tool absent or timed out; optional check

    return result
