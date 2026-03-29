from __future__ import annotations

import logging
import plistlib
from pathlib import Path

from ..base import BaseImporter
from ..models import ImportedItem
from ..utils import safari_default_path

logger = logging.getLogger(__name__)


class SafariImporter(BaseImporter):
    SOURCE_NAME = "safari"
    ITEM_TYPE = "bookmark"

    def detect(self) -> bool:
        default = self.default_path()
        return bool(default and Path(default).exists())

    def default_path(self) -> str | None:
        return safari_default_path()

    def parse(self, path: str) -> list[ImportedItem]:
        file_path = Path(path)
        if not file_path.exists():
            return []
        try:
            with file_path.open("rb") as fh:
                payload = plistlib.load(fh)

            items: list[ImportedItem] = []
            children = payload.get("Children", []) if isinstance(payload, dict) else []
            for child in children:
                self._walk(children=[child], folder_path=[], items=items)
            return items
        except Exception as exc:
            logger.warning("[%s] %s", self.SOURCE_NAME, exc)
            return []

    def _walk(self, children: list[dict], folder_path: list[str], items: list[ImportedItem]) -> None:
        for node in children:
            title = str(node.get("Title") or "")
            url = str(node.get("URLString") or "")
            nested = node.get("Children", [])

            if url:
                items.append(
                    ImportedItem(
                        source=self.SOURCE_NAME,
                        type=self.ITEM_TYPE,
                        label=title or url,
                        value=url,
                        meta={"folder": "/".join(folder_path)},
                    )
                )

            if isinstance(nested, list) and nested:
                next_path = folder_path + ([title] if title else [])
                self._walk(children=nested, folder_path=next_path, items=items)
