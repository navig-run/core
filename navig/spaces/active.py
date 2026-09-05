"""Active working-directory resolution for the agent (the cwd-binding fix).

Root cause of "agent in homelab writes to the repo root": nothing bound the
active space to the process cwd. This resolves the working directory the agent
should operate in, without a global ``os.chdir`` race, with the same precedence
everywhere:

    session pin → NAVIG_SPACE → active_space_dir.txt → find_app_root() → cwd

⚠ **The session pin is a seam, not a live mechanism: `set_session_cwd` has NO callers.**
Today the active space is *process-global* — `navig space switch` and the deck's
set-active-space route both write the one `active_space_dir.txt`, and every concurrent
conversation resolves through it. Per-session spaces would need a product decision first
(which chat operates in which space; nothing in a session key encodes one), so the pin is
kept as the correct place to bind that when it exists — and is described here as unused
rather than as isolation you can rely on.
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path

from navig.platform import paths

# Per-request/session logical cwd. Currently set by NOTHING (see the module docstring):
# it is the seam a future per-session space binding would use, and until then every
# lookup falls straight through to the process-global sources below.
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


def _record_unreadable(path: Path, exc: OSError) -> None:
    """Note that we could not tell which space is active. Never raises.

    An observation must not break the thing it observes — and this sits on the per-turn
    path, so a failure here would cost a turn rather than a log line.
    """
    try:
        from navig.core import incidents  # noqa: PLC0415

        incidents.record(
            incidents.ACTIVE_SPACE_UNREADABLE, path=str(path), error=str(exc)
        )
    except Exception:  # noqa: BLE001
        pass


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
        cand = paths.spaces_dir() / env
        if cand.is_dir():
            return cand

    f = _active_space_dir_file()
    if f.exists():
        # This file is written through `atomic_write_text` → `os.replace`, so a reader
        # that opens it DURING the replace gets a sharing violation — as does an
        # antivirus or backup agent briefly holding it. A bare read that swallowed the
        # OSError did not fail here: it fell through and answered a DIFFERENT directory,
        # silently moving the agent into the wrong space. Same asymmetry (retrying write,
        # non-retrying read) that `read_text_retrying` exists to close.
        try:
            from navig.core.yaml_io import read_text_retrying  # noqa: PLC0415

            d = Path(read_text_retrying(f).strip())
            if d.is_dir():
                return d
        except OSError as exc:
            # The file EXISTS and stayed unreadable through the retries, so a space WAS
            # chosen and we cannot tell which. Falling through is still the only option —
            # this runs per turn inside callers that catch everything, so raising would
            # just disable skills with no message — but it must not be SILENT.
            _record_unreadable(f, exc)

    root = paths.find_app_root()
    if root is not None:
        return root

    return (cwd or Path.cwd())
