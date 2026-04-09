"""
navig.agent.profiles — Profile isolation for agent personas (F-15).

Profiles provide namespaced directories for memory, wiki, and config so that
multiple agent instances (e.g. "work" vs "personal") maintain separate state.

Storage layout::

    ~/.navig/profiles/
    ├── default/          # implicit when no profile is set
    │   ├── config.yaml
    │   ├── memory/       # KeyFactStore + sessions
    │   └── wiki/
    ├── work/
    │   ├── config.yaml
    │   ├── memory/
    │   └── wiki/
    └── personal/
        └── ...

Activation:
    - ``NAVIG_PROFILE`` env var  (highest priority)
    - ``~/.navig/active_profile`` file  (sticky default)
    - Fallback: ``"default"`` profile  (uses ``~/.navig/`` root directly)

CLI integration (future)::

    navig profile list
    navig profile use <name>
    navig profile new <name>

Usage::

    from navig.agent.profiles import get_active_profile, Profile

    p = get_active_profile()
    print(p.memory_dir)    # ~/.navig/profiles/work/memory/
    print(p.wiki_dir)      # ~/.navig/profiles/work/wiki/
    print(p.config_path)   # ~/.navig/profiles/work/config.yaml
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

_DEFAULT_PROFILE = "default"
_ACTIVE_PROFILE_FILE = "active_profile"
_PROFILES_DIR = "profiles"


def _navig_home() -> Path:
    """Return ``~/.navig/``."""
    from navig.platform.paths import config_dir

    return config_dir()


# ─────────────────────────────────────────────────────────────
# Profile dataclass
# ─────────────────────────────────────────────────────────────

@dataclass
class Profile:
    """A named agent profile with isolated directories.

    Attributes:
        name:             Profile name (e.g. ``"work"``).
        home_dir:         Root directory for this profile.
        config_overrides: Extra config dict merged on top of global config.
        active_toolsets:  Default toolset list for agentic sessions.
    """

    name: str
    home_dir: Path
    config_overrides: dict[str, Any] = field(default_factory=dict)
    active_toolsets: list[str] = field(default_factory=lambda: ["core"])

    # ── Derived paths ──

    @property
    def memory_dir(self) -> Path:
        """Directory for KeyFactStore and session database."""
        return self.home_dir / "memory"

    @property
    def wiki_dir(self) -> Path:
        """Directory for wiki content."""
        return self.home_dir / "wiki"

    @property
    def config_path(self) -> Path:
        """Profile-specific config YAML."""
        return self.home_dir / "config.yaml"

    @property
    def is_default(self) -> bool:
        return self.name == _DEFAULT_PROFILE

    # ── Lifecycle ──

    def ensure_dirs(self) -> None:
        """Create profile directories if they don't exist."""
        self.home_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.wiki_dir.mkdir(parents=True, exist_ok=True)

    def load_config_overrides(self) -> dict[str, Any]:
        """Load and return profile-specific config overrides.

        Returns an empty dict if no config file exists.
        """
        if not self.config_path.exists():
            return {}
        try:
            from navig.core.yaml_io import safe_load_yaml

            data = safe_load_yaml(self.config_path)
            if isinstance(data, dict):
                self.config_overrides = data
                return data
        except Exception as exc:
            logger.debug("Failed to load profile config %s: %s", self.config_path, exc)
        return {}

    def save_config_overrides(self) -> None:
        """Persist current config_overrides to YAML."""
        try:
            import yaml

            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.config_overrides, f, default_flow_style=False)
        except Exception as exc:
            logger.warning("Failed to save profile config: %s", exc)


# ─────────────────────────────────────────────────────────────
# Profile resolution
# ─────────────────────────────────────────────────────────────

def _read_active_profile_name() -> str:
    """Read the sticky active profile from ``~/.navig/active_profile``."""
    path = _navig_home() / _ACTIVE_PROFILE_FILE
    if path.exists():
        try:
            name = path.read_text(encoding="utf-8").strip()
            if name:
                return name
        except Exception:
            pass  # best-effort: profile name file unreadable; use default
    return _DEFAULT_PROFILE


def _write_active_profile_name(name: str) -> None:
    """Persist the active profile name."""
    path = _navig_home() / _ACTIVE_PROFILE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name.strip(), encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not write active_profile: %s", exc)


def resolve_profile_name() -> str:
    """Determine the current profile name.

    Priority: ``NAVIG_PROFILE`` env → ``~/.navig/active_profile`` → ``"default"``.
    """
    env_name = os.environ.get("NAVIG_PROFILE", "").strip()
    if env_name:
        return env_name
    return _read_active_profile_name()


def _profile_home(name: str) -> Path:
    """Return the home directory for a given profile name.

    The ``"default"`` profile uses ``~/.navig/`` directly for backward
    compatibility.  All others use ``~/.navig/profiles/<name>/``.
    """
    home = _navig_home()
    if name == _DEFAULT_PROFILE:
        return home
    return home / _PROFILES_DIR / name


def get_profile(name: str) -> Profile:
    """Load a :class:`Profile` by name (does not ensure directories)."""
    home = _profile_home(name)
    p = Profile(name=name, home_dir=home)
    p.load_config_overrides()
    return p


def get_active_profile() -> Profile:
    """Return the currently active profile (resolved from env / sticky file).

    Creates directories if needed.
    """
    name = resolve_profile_name()
    p = get_profile(name)
    p.ensure_dirs()
    return p


# ─────────────────────────────────────────────────────────────
# Profile management
# ─────────────────────────────────────────────────────────────

def list_profiles() -> list[str]:
    """List all available profile names."""
    profiles: list[str] = [_DEFAULT_PROFILE]
    profiles_dir = _navig_home() / _PROFILES_DIR
    if profiles_dir.exists():
        for child in sorted(profiles_dir.iterdir()):
            if child.is_dir() and child.name not in profiles:
                profiles.append(child.name)
    return profiles


def create_profile(name: str, toolsets: list[str] | None = None) -> Profile:
    """Create a new profile (idempotent — safe to call if it exists).

    Args:
        name:     Profile name.
        toolsets: Default active toolsets for agentic sessions.

    Returns:
        The newly created (or existing) profile.
    """
    p = Profile(
        name=name,
        home_dir=_profile_home(name),
        active_toolsets=toolsets or ["core"],
    )
    p.ensure_dirs()
    p.save_config_overrides()
    logger.info("Created profile %r at %s", name, p.home_dir)
    return p


def switch_profile(name: str) -> Profile:
    """Set the active profile and persist the choice.

    Args:
        name: Profile name to activate (must exist or will be created).

    Returns:
        The now-active profile.
    """
    p = get_profile(name)
    p.ensure_dirs()
    _write_active_profile_name(name)
    logger.info("Switched to profile %r", name)
    return p


def delete_profile(name: str) -> bool:
    """Remove a profile directory.

    The ``"default"`` profile cannot be deleted.

    Args:
        name: Profile to delete.

    Returns:
        True if deleted, False otherwise.
    """
    if name == _DEFAULT_PROFILE:
        logger.warning("Cannot delete the default profile")
        return False

    home = _profile_home(name)
    if not home.exists():
        return False

    import shutil

    shutil.rmtree(home, ignore_errors=True)
    logger.info("Deleted profile %r", name)

    # If this was the active profile, reset to default
    if resolve_profile_name() == name:
        _write_active_profile_name(_DEFAULT_PROFILE)
    return True
