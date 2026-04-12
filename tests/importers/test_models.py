from navig.importers.models import ImportedItem, validate_item_dict
import pytest

pytestmark = pytest.mark.unit


def test_imported_item_to_dict_validates() -> None:
    item = ImportedItem(
        source="chrome",
        type="bookmark",
        label="Docs",
        value="https://example.com",
        meta={"folder": "bookmark_bar/dev"},
    )
    data = item.to_dict()
    assert data["source"] == "chrome"
    assert data["type"] == "bookmark"


def test_validate_item_dict_rejects_missing_fields() -> None:
    try:
        validate_item_dict({"source": "x"})
        assert False, "expected ValueError"
    except ValueError:
        assert True
