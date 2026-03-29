from __future__ import annotations

import json
import logging
from pathlib import Path

from .base import BaseImporter
from .models import ImportedItem
from .sources import (
    ChromeImporter,
    EdgeImporter,
    FirefoxImporter,
    SafariImporter,
    TelegramImporter,
    WinSCPImporter,
)

logger = logging.getLogger(__name__)


class UniversalImporter:
    def __init__(self, importers: list[BaseImporter] | None = None):
        self._importers = importers or [
            WinSCPImporter(),
            TelegramImporter(),
            ChromeImporter(),
            FirefoxImporter(),
            EdgeImporter(),
            SafariImporter(),
        ]
        self._by_source = {imp.SOURCE_NAME: imp for imp in self._importers}

    def list_sources(self) -> list[str]:
        return sorted(self._by_source.keys())

    def infer_source(self, path: str) -> str | None:
        probe = path.lower()
        if probe.endswith("winscp.ini") or probe.endswith(".reg"):
            return "winscp"
        if probe.endswith("contacts.json"):
            return "telegram"
        if probe.endswith("places.sqlite"):
            return "firefox"
        if probe.endswith("bookmarks.plist"):
            return "safari"
        if probe.endswith("bookmarks"):
            if "microsoft" in probe or "edge" in probe:
                return "edge"
            return "chrome"
        return None

    def run_all(self) -> dict[str, list[ImportedItem]]:
        results: dict[str, list[ImportedItem]] = {}
        for importer in self._importers:
            try:
                results[importer.SOURCE_NAME] = importer.run()
            except Exception as exc:
                logger.warning("[%s] %s", importer.SOURCE_NAME, exc)
                results[importer.SOURCE_NAME] = []
        return results

    def run_one(self, source: str, path: str | None = None) -> list[ImportedItem]:
        importer = self._by_source.get(source.lower())
        if importer is None:
            raise ValueError(
                f"Unknown import source '{source}'. Available: {', '.join(self.list_sources())}"
            )
        if path is not None and not Path(path).exists():
            raise FileNotFoundError(f"Import path does not exist: {path}")
        try:
            return importer.run(path)
        except Exception as exc:
            logger.warning("[%s] %s", source, exc)
            return []

    def run_path(self, path: str) -> tuple[str, list[ImportedItem]]:
        source = self.infer_source(path)
        if source is None:
            raise ValueError(f"Could not infer import source from path: {path}")
        return source, self.run_one(source, path=path)

    def export_json(self, results: dict[str, list[ImportedItem]]) -> str:
        payload = {
            source: [item.to_dict() for item in items]
            for source, items in results.items()
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
