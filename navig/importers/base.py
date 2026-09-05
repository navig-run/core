from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from .models import ImportedItem

logger = logging.getLogger(__name__)


class BaseImporter(ABC):
    SOURCE_NAME: str
    ITEM_TYPE: str

    #: Why the last :meth:`run` produced nothing, or ``None`` if it genuinely had nothing
    #: to import. Every ``parse()`` degrades a read failure to an empty list so one bad
    #: source can't abort a multi-source run — but "the read FAILED" and "there are no
    #: bookmarks" then look identical to the caller, so the CLI reported a locked
    #: ``places.sqlite`` (Firefox merely being OPEN) as a successful empty import and
    #: exited 0. Recording the reason here is what lets a caller tell them apart.
    last_error: str | None = None

    def _fail(self, exc: BaseException) -> list[ImportedItem]:
        """Record + log a read failure and degrade to no items. The ONE failure path —
        a ``parse()`` that swallows an exception without going through here is invisible
        to :class:`~navig.importers.core.UniversalImporter` and reports as success."""
        self.last_error = f"{type(exc).__name__}: {exc}"
        logger.warning("[%s] %s", self.SOURCE_NAME, exc)
        return []

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
        self.last_error = None  # a fresh verdict per run — never report a stale failure
        resolved = path or self.default_path()
        if not resolved:
            return []

        candidate = Path(resolved)
        if not candidate.exists():
            return []

        if path is None and not self.detect():
            return []

        return self.parse(str(candidate))
