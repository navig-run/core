import plistlib

from navig.importers.sources.safari import SafariImporter
import pytest

pytestmark = pytest.mark.integration


def test_safari_bookmarks_parse(tmp_path) -> None:
    path = tmp_path / "Bookmarks.plist"
    payload = {
        "Children": [
            {
                "Title": "Favorites",
                "Children": [
                    {"Title": "NAVIG", "URLString": "https://example.com"},
                ],
            }
        ]
    }
    with path.open("wb") as fh:
        plistlib.dump(payload, fh)

    items = SafariImporter().parse(str(path))
    assert len(items) == 1
    assert items[0].label == "NAVIG"
    assert items[0].meta["folder"] == "Favorites"
