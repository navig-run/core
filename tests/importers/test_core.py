from navig.importers.base import BaseImporter
from navig.importers.core import UniversalImporter
from navig.importers.models import ImportedItem
import pytest
import json
from zipfile import ZipFile


class _OkImporter(BaseImporter):
    SOURCE_NAME = "ok"
    ITEM_TYPE = "bookmark"

    def detect(self) -> bool:
        return True

    def default_path(self) -> str | None:
        return None

    def parse(self, path: str) -> list[ImportedItem]:
        return [ImportedItem(source="ok", type="bookmark", label="a", value="https://a", meta={})]

    def run(self, path: str | None = None) -> list[ImportedItem]:
        return self.parse(path or "")


class _FailImporter(_OkImporter):
    SOURCE_NAME = "fail"

    def run(self, path: str | None = None) -> list[ImportedItem]:
        raise RuntimeError("boom")


def test_run_all_isolates_failures() -> None:
    engine = UniversalImporter(importers=[_OkImporter(), _FailImporter()])
    results = engine.run_all()
    assert len(results["ok"]) == 1
    assert results["fail"] == []


def test_export_json() -> None:
    engine = UniversalImporter(importers=[_OkImporter()])
    data = engine.export_json(engine.run_all())
    assert '"ok"' in data
    assert '"bookmark"' in data


def test_run_one_unknown_source_raises() -> None:
    engine = UniversalImporter(importers=[_OkImporter()])
    with pytest.raises(ValueError):
        engine.run_one("missing")


def test_run_one_missing_path_raises(tmp_path) -> None:
    engine = UniversalImporter(importers=[_OkImporter()])
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError):
        engine.run_one("ok", path=str(missing))


def test_infer_source_does_not_claim_unknown_zip() -> None:
    engine = UniversalImporter(importers=[_OkImporter()])
    assert engine.infer_source("export.zip") is None


def test_infer_source_detects_telegram_zip(tmp_path) -> None:
    engine = UniversalImporter(importers=[_OkImporter()])
    z = tmp_path / "telegram_export.zip"
    with ZipFile(z, "w") as archive:
        archive.writestr("contacts.json", '{"contacts": []}')

    assert engine.infer_source(str(z)) == "telegram"


def test_infer_source_detects_bookmark_json(tmp_path) -> None:
    engine = UniversalImporter(importers=[_OkImporter()])
    path = tmp_path / "Bookmarks.json"
    path.write_text(
        json.dumps({"roots": {"bookmark_bar": {"children": []}}}),
        encoding="utf-8",
    )

    assert engine.infer_source(str(path)) == "chrome"
