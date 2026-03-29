from __future__ import annotations

import json
import logging
from pathlib import Path
from zipfile import ZipFile

from ..base import BaseImporter
from ..models import ImportedItem

logger = logging.getLogger(__name__)


class TelegramImporter(BaseImporter):
    SOURCE_NAME = "telegram"
    ITEM_TYPE = "contact"

    def detect(self) -> bool:
        return False

    def default_path(self) -> str | None:
        return None

    def parse(self, path: str) -> list[ImportedItem]:
        candidate = Path(path)
        if not candidate.exists():
            return []
        try:
            payload = self._load_contacts_payload(candidate)
            if payload is None:
                return []
            contacts = payload.get("contacts", [])
            items: list[ImportedItem] = []
            for row in contacts:
                contact = row.get("contact") if isinstance(row, dict) else None
                if isinstance(contact, dict):
                    first_name = str(contact.get("first_name") or "").strip()
                    last_name = str(contact.get("last_name") or "").strip()
                    phone = str(contact.get("phone_number") or contact.get("phone") or "").strip()
                else:
                    first_name = str(row.get("first_name") or "").strip() if isinstance(row, dict) else ""
                    last_name = str(row.get("last_name") or "").strip() if isinstance(row, dict) else ""
                    phone = str(row.get("phone_number") or row.get("phone") or "").strip() if isinstance(row, dict) else ""

                if not phone and not (first_name or last_name):
                    continue
                label = (f"{first_name} {last_name}").strip() or phone
                items.append(
                    ImportedItem(
                        source=self.SOURCE_NAME,
                        type=self.ITEM_TYPE,
                        label=label,
                        value=phone,
                        meta={
                            "first_name": first_name,
                            "last_name": last_name,
                        },
                    )
                )
            return items
        except Exception as exc:
            logger.warning("[%s] %s", self.SOURCE_NAME, exc)
            return []

    def _load_contacts_payload(self, candidate: Path) -> dict | None:
        if candidate.is_dir():
            contacts = candidate / "contacts.json"
            if not contacts.exists():
                return None
            return json.loads(contacts.read_text(encoding="utf-8"))

        if candidate.suffix.lower() == ".zip":
            with ZipFile(candidate) as archive:
                for member in archive.namelist():
                    if member.endswith("contacts.json"):
                        raw = archive.read(member).decode("utf-8")
                        return json.loads(raw)
            return None

        if candidate.name.lower() == "contacts.json":
            return json.loads(candidate.read_text(encoding="utf-8"))

        return None
