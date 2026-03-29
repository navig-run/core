import json

from navig.importers.sources.chrome import ChromeImporter
from navig.importers.sources.edge import EdgeImporter


def _sample_bookmarks() -> dict:
    return {
        "roots": {
            "bookmark_bar": {
                "children": [
                    {
                        "type": "folder",
                        "name": "Dev",
                        "children": [
                            {"type": "url", "name": "Docs", "url": "https://example.com/docs"}
                        ],
                    }
                ]
            },
            "other": {"children": []},
            "synced": {"children": []},
        }
    }


def test_chrome_parse(tmp_path) -> None:
    p = tmp_path / "Bookmarks"
    p.write_text(json.dumps(_sample_bookmarks()), encoding="utf-8")

    items = ChromeImporter().parse(str(p))
    assert len(items) == 1
    assert items[0].source == "chrome"
    assert items[0].meta["folder"] == "bookmark_bar/Dev"


def test_edge_parse(tmp_path) -> None:
    p = tmp_path / "Bookmarks"
    p.write_text(json.dumps(_sample_bookmarks()), encoding="utf-8")

    items = EdgeImporter().parse(str(p))
    assert len(items) == 1
    assert items[0].source == "edge"
