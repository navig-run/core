from __future__ import annotations

import json
import logging
from pathlib import Path

from ..base import BaseImporter
from ..models import ImportedItem
from ..utils import chrome_default_path

logger = logging.getLogger(__name__)


class ChromeImporter(BaseImporter):
    SOURCE_NAME = "chrome"
    ITEM_TYPE = "bookmark"

    def detect(self) -> bool:
        default = self.default_path()
        return bool(default and Path(default).exists())

    def default_path(self) -> str | None:
        return chrome_default_path()

    def parse(self, path: str) -> list[ImportedItem]:
        source = Path(path)
        if not source.exists():
            return []
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
            roots = payload.get("roots", {})
            items: list[ImportedItem] = []

            bookmark_bar = roots.get("bookmark_bar", {})
            for child in bookmark_bar.get("children", []):
                self._walk_node(items, child, ["bookmark_bar"])

            other = roots.get("other", {})
            for child in other.get("children", []):
                self._walk_node(items, child, ["other"])

            synced = roots.get("synced", {})
            for child in synced.get("children", []):
                self._walk_node(items, child, ["synced"])

            return items
        except Exception as exc:
            logger.warning("[%s] %s", self.SOURCE_NAME, exc)
            return []

    def _walk_node(self, items: list[ImportedItem], node: dict, folder_path: list[str]) -> None:
        node_type = node.get("type")
        if node_type == "folder":
            name = str(node.get("name") or "folder")
            for child in node.get("children", []):
                self._walk_node(items, child, folder_path + [name])
            return

        if node_type == "url":
            title = str(node.get("name") or node.get("url") or "bookmark")
            url = str(node.get("url") or "")
            if not url:
                return
            items.append(
                ImportedItem(
                    source=self.SOURCE_NAME,
                    type=self.ITEM_TYPE,
                    label=title,
                    value=url,
                    meta={"folder": "/".join(folder_path)},
                )
            )
