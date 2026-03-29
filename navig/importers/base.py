from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .models import ImportedItem


class BaseImporter(ABC):
    SOURCE_NAME: str
    ITEM_TYPE: str

    @abstractmethod
    def detect(self) -> bool:
        """Return True if the source file/path exists on this system."""

    @abstractmethod
    def parse(self, path: str) -> list[ImportedItem]:
        """Parse the source at the given path and return normalized items."""

    @abstractmethod
    def default_path(self) -> str | None:
        """Return the OS-default path for this source, or None if unknown."""

    def run(self, path: str | None = None) -> list[ImportedItem]:
        resolved = path or self.default_path()
        if not resolved:
            return []

        candidate = Path(resolved)
        if not candidate.exists():
            return []

        if path is None and not self.detect():
            return []

        return self.parse(str(candidate))
