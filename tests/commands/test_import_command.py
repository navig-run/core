from __future__ import annotations

import pytest
from typer.testing import CliRunner

from navig.commands.import_cmd import import_app

pytestmark = pytest.mark.integration

runner = CliRunner()


def test_import_unknown_source_fails() -> None:
    result = runner.invoke(import_app, ["--source", "unknown"])
    assert result.exit_code == 1
    assert "Unknown source" in result.output


def test_import_source_all_with_path_fails(tmp_path) -> None:
    p = tmp_path / "dummy.json"
    p.write_text("{}", encoding="utf-8")

    result = runner.invoke(import_app, ["--source", "all", "--path", str(p)])
    assert result.exit_code == 1
    assert "cannot be used with --source all" in result.output


def test_import_missing_path_fails(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    result = runner.invoke(import_app, ["--source", "chrome", "--path", str(missing)])
    assert result.exit_code == 1
    assert "does not exist" in result.output


# ── Safari title self-repair (dedupe-on-url made the damage sticky) ────────────


def test_persist_repairs_a_bookmark_whose_title_is_its_url(tmp_path, monkeypatch):
    """The pre-fix Safari parser stored every bookmark with its own URL as the title.
    Both persist paths skip on get_by_url, so a re-import after the parser fix would
    count them as duplicates and NEVER repair them. Only that exact signature is
    repaired; a genuinely-titled duplicate is still just skipped."""
    from navig.commands import import_cmd
    from navig.importers.models import ImportedItem
    from navig.memory.links_db import LinksDB

    db = LinksDB(tmp_path / "links.db")
    broken = db.add("https://www.anthropic.com/", title="https://www.anthropic.com/")
    intact = db.add("https://example.com/", title="Real Title")
    monkeypatch.setattr(import_cmd.links_db_mod, "get_links_db", lambda: db)

    def _item(url: str, label: str) -> ImportedItem:
        return ImportedItem(source="safari", type="bookmark", label=label, value=url)

    results = {
        "safari": [
            # now parsed correctly → repairs the broken row
            _item("https://www.anthropic.com/", "Anthropic"),
            # a plain duplicate → still skipped, title untouched
            _item("https://example.com/", "Something Else"),
        ]
    }

    added, skipped, repaired = import_cmd._persist_bookmarks(results)

    assert (added, skipped, repaired) == (0, 1, 1)
    assert db.get(broken).title == "Anthropic"
    assert db.get(intact).title == "Real Title"  # never clobbered


# ── an unreadable source is a FAILURE, not an empty import ────────────────────


def _unreadable_places(tmp_path):
    """A places.sqlite that exists but can't be read — what a LOCKED Firefox profile
    (i.e. Firefox merely being open), a partial copy, or a corrupt file looks like."""
    p = tmp_path / "places.sqlite"
    p.write_bytes(b"\x00\x01 not a sqlite file \xff")
    return p


class TestImportFailureIsNotEmptySuccess:
    def test_engine_records_why_a_source_produced_nothing(self, tmp_path):
        from navig.importers.core import UniversalImporter

        engine = UniversalImporter()
        assert engine.run_one("firefox", path=str(_unreadable_places(tmp_path))) == []
        assert "firefox" in engine.errors  # pre-fix: silently indistinguishable from empty

    def test_a_genuinely_empty_source_is_not_an_error(self, tmp_path):
        import plistlib

        from navig.importers.core import UniversalImporter

        p = tmp_path / "Bookmarks.plist"
        p.write_bytes(plistlib.dumps({"Children": []}))

        engine = UniversalImporter()
        assert engine.run_one("safari", path=str(p)) == []
        assert engine.errors == {}  # no false alarm — nothing to import is not a failure

    def test_last_error_does_not_leak_into_the_next_run(self, tmp_path):
        """A stale failure must not condemn a later healthy run."""
        import plistlib

        from navig.importers.sources.safari import SafariImporter

        bad = tmp_path / "bad.plist"
        bad.write_bytes(b"definitely not a plist")
        good = tmp_path / "good.plist"
        good.write_bytes(plistlib.dumps({"Children": []}))

        imp = SafariImporter()
        imp.run(str(bad))
        assert imp.last_error is not None
        imp.run(str(good))
        assert imp.last_error is None

    def test_cli_reports_the_failure_and_exits_nonzero(self, tmp_path):
        result = runner.invoke(
            import_app,
            ["--source", "firefox", "--path", str(_unreadable_places(tmp_path)),
             "--no-persist-bookmarks"],
        )
        # pre-fix: "No items imported." and exit 0 — a script saw a successful import.
        assert result.exit_code == 1
        assert "could not read source" in result.output

    def test_cli_still_exits_zero_for_a_genuinely_empty_source(self, tmp_path):
        import plistlib

        p = tmp_path / "Bookmarks.plist"
        p.write_bytes(plistlib.dumps({"Children": []}))

        result = runner.invoke(
            import_app, ["--source", "safari", "--path", str(p), "--no-persist-bookmarks"]
        )
        assert result.exit_code == 0
        assert "No items imported" in result.output

    def test_json_mode_keeps_stdout_parseable_and_still_fails(self, tmp_path):
        import json

        result = runner.invoke(
            import_app,
            ["--source", "firefox", "--path", str(_unreadable_places(tmp_path)),
             "--no-persist-bookmarks", "--json"],
        )
        assert result.exit_code == 1
        json.loads(result.stdout)  # stdout is still exactly one JSON document
