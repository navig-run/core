import sqlite3

from navig.importers.sources.firefox import FirefoxImporter


def test_firefox_places_parse(tmp_path) -> None:
    db = tmp_path / "places.sqlite"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    con.execute(
        "CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, fk INTEGER, type INTEGER, parent INTEGER, title TEXT)"
    )
    con.execute("INSERT INTO moz_places(id, url, title) VALUES (1, 'https://example.com', 'Example')")
    con.execute("INSERT INTO moz_bookmarks(id, fk, type, parent, title) VALUES (2, NULL, 2, 0, 'toolbar')")
    con.execute("INSERT INTO moz_bookmarks(id, fk, type, parent, title) VALUES (3, 1, 1, 2, 'Example')")
    con.commit()
    con.close()

    items = FirefoxImporter().parse(str(db))
    assert len(items) == 1
    assert items[0].value == "https://example.com"
    assert items[0].meta["folder"] == "toolbar"
