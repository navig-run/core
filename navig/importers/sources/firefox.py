from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from ..base import BaseImporter
from ..models import ImportedItem
from ..utils import firefox_places_default_path

logger = logging.getLogger(__name__)


class FirefoxImporter(BaseImporter):
    SOURCE_NAME = "firefox"
    ITEM_TYPE = "bookmark"

    def detect(self) -> bool:
        default = self.default_path()
        return bool(default and Path(default).exists())

    def default_path(self) -> str | None:
        return firefox_places_default_path()

    def parse(self, path: str) -> list[ImportedItem]:
        db = Path(path)
        if not db.exists():
            return []
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row

            folders = {
                row["id"]: {"title": row["title"] or "", "parent": row["parent"]}
                for row in con.execute(
                    "SELECT id, parent, title FROM moz_bookmarks WHERE type = 2"
                ).fetchall()
            }

            rows = con.execute(
                """
                SELECT b.id, b.parent, COALESCE(b.title, p.title, p.url) AS title, p.url
                FROM moz_bookmarks b
                JOIN moz_places p ON b.fk = p.id
                WHERE b.type = 1
                """
            ).fetchall()

            items: list[ImportedItem] = []
            for row in rows:
                url = row["url"] or ""
                if not url:
                    continue
                folder = self._resolve_folder_chain(row["parent"], folders)
                items.append(
                    ImportedItem(
                        source=self.SOURCE_NAME,
                        type=self.ITEM_TYPE,
                        label=row["title"] or url,
                        value=url,
                        meta={"folder": folder},
                    )
                )

            con.close()
            return items
        except Exception as exc:
            logger.warning("[%s] %s", self.SOURCE_NAME, exc)
            return []

    def _resolve_folder_chain(self, parent_id: int, folders: dict[int, dict]) -> str:
        chain: list[str] = []
        seen: set[int] = set()
        current = parent_id
        while current in folders and current not in seen:
            seen.add(current)
            info = folders[current]
            title = str(info.get("title") or "")
            if title:
                chain.append(title)
            current = int(info.get("parent") or 0)
        chain.reverse()
        return "/".join(chain)
