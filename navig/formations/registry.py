"""
Formation Registry Singleton

Loads formations once at gateway startup to prevent redundant per-request disk scans.
"""

import logging
from pathlib import Path

from navig.formations.loader import discover_formations, get_active_formation
from navig.formations.types import Formation

logger = logging.getLogger(__name__)


class FormationRegistry:
    """Singleton registry for caching the active formation and discovered formations."""

    _instance = None

    def __init__(self):
        self._active_formation: Formation | None = None
        self._formation_map: dict[str, Path] = {}
        self._initialized: bool = False

    @classmethod
    def get_instance(cls) -> "FormationRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self, workspace_dir: Path | None = None) -> None:
        """Scan and load formations from disk once."""
        if self._initialized:
            return

        logger.info("[FORMATION] Initializing formation registry...")

        # Discover all available formations
        self._formation_map = discover_formations()

        # Resolve and load the active formation
        self._active_formation = get_active_formation(workspace_dir)

        if self._active_formation:
            logger.info(
                f"[FORMATION] Registry loaded active formation: {self._active_formation.name}"
            )
        else:
            logger.warning("[FORMATION] Registry initialized but no active formation was found.")

        self._initialized = True

    def get_active(self) -> Formation | None:
        """Get the cached active formation."""
        return self._active_formation

    def get_formation_map(self) -> dict[str, Path]:
        """Get the cached map of discovered formations."""
        return self._formation_map

    def reload(self, workspace_dir: Path | None = None) -> None:
        """Force a reload of formations from disk."""
        self._initialized = False
        self.initialize(workspace_dir)


def get_registry() -> FormationRegistry:
    """Get the global FormationRegistry instance."""
    return FormationRegistry.get_instance()
