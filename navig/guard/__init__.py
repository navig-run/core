"""navig.guard — canonical templates for the multi-agent repo guard hooks.

``agent_lock.py`` and ``session_start.py`` here are the SINGLE SOURCE for the
Claude Code hooks that ``navig repo guard install`` writes into a target
repo's ``.claude/hooks/``. They are stdlib-only, standalone scripts (never
import navig) so they keep working in repos where no venv exists.

The navig repo's own live copies in ``scripts/agent-hooks/`` must stay
byte-identical — ``core/tests/repo/test_guard_installer.py`` enforces it.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

HOOK_FILES = ("agent_lock.py", "session_start.py")


def template_text(name: str) -> str:
    """Return a hook template's source text (``name`` in :data:`HOOK_FILES`)."""
    if name not in HOOK_FILES:
        raise ValueError(f"unknown guard hook template: {name}")
    return (resources.files(__package__) / name).read_text(encoding="utf-8")


def template_path(name: str) -> Path:
    """Filesystem path of a template (source checkouts / editable installs)."""
    if name not in HOOK_FILES:
        raise ValueError(f"unknown guard hook template: {name}")
    return Path(__file__).resolve().parent / name
