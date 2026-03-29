import json
from zipfile import ZipFile

from navig.importers.sources.telegram import TelegramImporter


def _payload() -> dict:
    return {
        "contacts": [
            {"first_name": "Ada", "last_name": "L", "phone_number": "+100"},
            {"contact": {"first_name": "Linus", "last_name": "T", "phone_number": "+200"}},
        ]
    }


def test_telegram_contacts_from_dir(tmp_path) -> None:
    d = tmp_path / "export"
    d.mkdir()
    (d / "contacts.json").write_text(json.dumps(_payload()), encoding="utf-8")

    items = TelegramImporter().parse(str(d))
    assert len(items) == 2
    assert items[0].type == "contact"


def test_telegram_contacts_from_zip(tmp_path) -> None:
    z = tmp_path / "export.zip"
    with ZipFile(z, "w") as archive:
        archive.writestr("contacts.json", json.dumps(_payload()))

    items = TelegramImporter().parse(str(z))
    assert len(items) == 2
    assert items[1].value == "+200"
