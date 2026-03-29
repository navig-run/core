from navig.importers.base import BaseImporter
from navig.importers.core import UniversalImporter
from navig.importers.models import ImportedItem


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
