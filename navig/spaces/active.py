"""Active working-directory resolution for the agent (the cwd-binding fix).

Root cause of "agent in homelab writes to the repo root": nothing bound the
active space to the process cwd. This resolves the working directory the agent
should operate in, gateway-safe (a session ContextVar instead of a global
``os.chdir`` race), with the same precedence everywhere:

    session pin (gateway) → NAVIG_SPACE → active_space_dir.txt → find_app_root() → cwd
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path

from navig.platform import paths

# Per-request/session logical cwd — set by the gateway so concurrent spaces never
# fight over a single process cwd. The CLI may also `os.chdir` once at startup.
_SESSION_CWD: ContextVar[str | None] = ContextVar("navig_session_cwd", default=None)


def set_session_cwd(path: str | Path | None) -> None:
    _SESSION_CWD.set(str(path) if path else None)


def _active_space_dir_file() -> Path:
    return paths.config_dir() / "cache" / "active_space_dir.txt"


def set_active_working_dir(path: str | Path) -> None:
    """Persist the active workshop's working directory (used by `navig space switch`)."""
    from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

    f = _active_space_dir_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(f, str(path))


def get_active_working_dir(cwd: Path | None = None) -> Path:
    """Return the directory the agent should treat as its working dir."""
    pin = _SESSION_CWD.get()
    if pin and Path(pin).is_dir():
        return Path(pin)

    env = os.environ.get("NAVIG_SPACE", "").strip()
    if env:
        ep = Path(env).expanduser()
        if ep.is_dir():
            return ep
        cand = paths.config_dir() / "spaces" / env
        if cand.is_dir():
            return cand

    f = _active_space_dir_file()
    if f.exists():
        try:
            d = Path(f.read_text(encoding="utf-8").strip())
            if d.is_dir():
                return d
        except OSError:
            pass

    root = paths.find_app_root()
    if root is not None:
        return root

    return (cwd or Path.cwd())
