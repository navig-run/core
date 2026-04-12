"""
navig.tui.config_model — Shared NavigConfig dataclass and related helpers.

No Textual dependency.  Safe to import anywhere.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from navig.platform.paths import config_dir
from navig.workspace_ownership import USER_WORKSPACE_DIR

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------
DEFAULT_NAVIG_DIR: Path = config_dir()
DEFAULT_WORKSPACE_DIR: Path = USER_WORKSPACE_DIR
DEFAULT_CONFIG_FILE: Path = DEFAULT_NAVIG_DIR / "navig.json"


# ---------------------------------------------------------------------------
# NavigConfig — single source of truth across all wizard steps
# ---------------------------------------------------------------------------


@dataclass
class NavigConfig:
    """Mutable config object shared by reference across all wizard steps."""

    # Step 1 — Identity
    profile_name: str = "operator"
    workspace_root: str = str(DEFAULT_WORKSPACE_DIR)
    theme: str = "dark"

    # Step 2 — Provider
    ai_provider: str = "openrouter"
    api_key: str = ""

    # Step 3 — Runtime
    local_runtime_enabled: bool = False
    local_runtime_host: str = "http://localhost:11434"

    # Step 4 — Packs
    capability_packs: list[str] = field(default_factory=list)

    # Step 5 — Shell & hooks
    shell_integration: bool = True
    auto_update: bool = True
    git_hooks: bool = False
    telemetry: bool = False

    # Optional integration preferences (used by full-tier onboarding)
    setup_matrix: bool = False
    setup_email: bool = False
    setup_social: bool = False

    # Tier selection (premium TUI): essential | recommended | full
    onboarding_tier: str = "recommended"


def build_config_dict(cfg: NavigConfig) -> dict[str, Any]:
    """Convert NavigConfig → JSON-serialisable dict matching navig.json schema."""
    return {
        "meta": {
            "version": "1.0.0",
            "created": datetime.now().isoformat(),
            "onboarding_flow": "tui-wizard",
            "onboarding_tier": cfg.onboarding_tier,
            "profile_name": cfg.profile_name,
        },
        "agents": {
            "defaults": {
                "workspace": cfg.workspace_root,
                "model": cfg.ai_provider,
                "typing_mode": "instant",
            }
        },
        "auth": {"profiles": {}},
        "channels": {},
        "runtime": {
            "local_enabled": cfg.local_runtime_enabled,
            "local_host": cfg.local_runtime_host if cfg.local_runtime_enabled else "",
        },
        "capabilities": cfg.capability_packs,
        "shell": {
            "integration": cfg.shell_integration,
            "auto_update": cfg.auto_update,
            "git_hooks": cfg.git_hooks,
            "telemetry": cfg.telemetry,
        },
        "onboarding": {
            "integrations": {
                "matrix": cfg.setup_matrix,
                "email": cfg.setup_email,
                "social": cfg.setup_social,
            }
        },
    }


def load_navig_json() -> dict[str, Any] | None:
    """Load ~/.navig/navig.json if it exists, else None."""
    try:
        if DEFAULT_CONFIG_FILE.is_file():
            return json.loads(DEFAULT_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------------
# Environment and system probes (no Textual dependency)
# ---------------------------------------------------------------------------


def detect_environment() -> dict[str, str]:
    """Return a snapshot of the local operator environment."""
    shell = os.environ.get("SHELL") or os.environ.get("COMSPEC") or "unknown"
    return {
        "hostname": socket.gethostname(),
        "shell": shell,
        "os": platform.system(),
        "python": platform.python_version(),
        "mode": "local",
        "status": "unbound",
    }


def check_python_version() -> bool:
    """Python >= 3.10 required."""
    return sys.version_info >= (3, 10)


def check_git_installed() -> bool:
    """True if git is on PATH."""
    return shutil.which("git") is not None


def check_network() -> bool:
    """DNS resolution probe."""
    try:
        socket.gethostbyname("example.com")
        return True
    except OSError:
        return False


def check_disk_space(min_mb: int = 100) -> bool:
    """At least min_mb of free disk space in home dir."""
    try:
        usage = shutil.disk_usage(Path.home())
        return (usage.free / 1024 / 1024) >= min_mb
    except OSError:
        return False


def check_config_dir_writable() -> bool:
    """~/.navig is writable (create-on-demand)."""
    try:
        navig_dir = config_dir()
        navig_dir.mkdir(parents=True, exist_ok=True)
        probe = navig_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def check_ollama_reachable(host: str = "http://localhost:11434") -> bool:
    """True if Ollama HTTP endpoint responds within 2 s."""
    try:
        import httpx  # already a core dep

        resp = httpx.get(host, timeout=2.0)
        return resp.status_code < 500
    except Exception:  # noqa: BLE001
        return False


from navig.providers.source_scan import check_api_key_in_env  # noqa: PLC0415
