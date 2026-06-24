"""Batch 117: tests for navig/commands/docs_cmd.py and navig/commands/import_cmd.py."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
import typer


# ---------------------------------------------------------------------------
# typer.Exit is click.exceptions.Exit, not SystemExit — catch both
# ---------------------------------------------------------------------------
_EXIT = (typer.Exit, SystemExit)


def _ctx(json_out: bool = False, raw: bool = False) -> MagicMock:
    ctx = MagicMock()
    ctx.obj = {"json": json_out, "raw": raw}
    return ctx


# ---------------------------------------------------------------------------
# Check if navig.tools.web is importable
# ---------------------------------------------------------------------------

def _has_tools_web() -> bool:
    try:
        import navig.tools.web  # noqa: F401
        return True
    except Exception:
        return False


# ===========================================================================
# run_docs — list mode (no query)
# The real docs/ directory is in the repo root so the function finds it and
# raises typer.Exit() naturally — no Path mocking needed.
# ===========================================================================

class TestRunDocsList:
    def test_list_exits_without_query(self):
        """run_docs with no query lists topics and raises Exit."""
        from navig.commands.docs_cmd import run_docs

        with pytest.raises(_EXIT):
            run_docs(_ctx(), None, 10, False, False)

    def test_list_json_exits(self):
        """run_docs with no query + json flag still raises Exit."""
        from navig.commands.docs_cmd import run_docs

        with pytest.raises(_EXIT):
            run_docs(_ctx(json_out=True), None, 10, False, True)

    def test_no_docs_dir_exits(self, tmp_path, monkeypatch):
        """When there is no docs dir, run_docs raises Exit(1)."""
        from navig.commands.docs_cmd import run_docs
        import navig.commands.docs_cmd as _docs_mod

        # Point __file__ to a dir with no docs/ subdir
        fake_dir = tmp_path / "pkg" / "commands"
        fake_dir.mkdir(parents=True)
        monkeypatch.setattr(_docs_mod, "__file__", str(fake_dir / "docs_cmd.py"))

        import navig.console_helper as _ch
        with patch.object(_ch, "error", create=True, return_value=None):
            with pytest.raises(Exception):  # typer.Exit or SystemExit
                run_docs(_ctx(), None, 10, False, False)


# ===========================================================================
# run_docs — search mode (with query)
# The real docs/ is found; only search_docs is mocked.
# ===========================================================================

class TestRunDocsSearch:
    def _fake_results(self):
        return [{"file": "README.md", "title": "Guide", "excerpt": "Content here", "score": 0.9}]

    def _with_docs_dir(self, monkeypatch, tmp_path):
        """Return a context: monkeypatches __file__ so docs/ is found."""
        import navig.commands.docs_cmd as _docs_mod
        docs_dir = tmp_path / "pkg" / "docs"
        docs_dir.mkdir(parents=True)
        (docs_dir / "README.md").write_text("# Guide\nContent", encoding="utf-8")
        # __file__ = tmp_path/pkg/commands/docs_cmd.py → parent.parent = tmp_path/pkg
        cmd_dir = tmp_path / "pkg" / "commands"
        cmd_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(_docs_mod, "__file__", str(cmd_dir / "docs_cmd.py"))

    @pytest.mark.skipif(not _has_tools_web(), reason="navig.tools.web not available")
    def test_search_with_results_rich(self, monkeypatch, tmp_path):
        from navig.commands.docs_cmd import run_docs
        self._with_docs_dir(monkeypatch, tmp_path)

        with patch("navig.tools.web.search_docs", return_value=self._fake_results()):
            run_docs(_ctx(), "guide", 5, False, False)

    @pytest.mark.skipif(not _has_tools_web(), reason="navig.tools.web not available")
    def test_search_no_results(self, monkeypatch, tmp_path):
        from navig.commands.docs_cmd import run_docs
        self._with_docs_dir(monkeypatch, tmp_path)

        with patch("navig.tools.web.search_docs", return_value=[]):
            run_docs(_ctx(), "nothing", 5, False, False)

    @pytest.mark.skipif(not _has_tools_web(), reason="navig.tools.web not available")
    def test_search_json_output(self, monkeypatch, tmp_path):
        from navig.commands.docs_cmd import run_docs
        self._with_docs_dir(monkeypatch, tmp_path)

        with patch("navig.tools.web.search_docs", return_value=self._fake_results()):
            run_docs(_ctx(json_out=True), "guide", 5, False, True)

    def test_search_exception_exits(self):
        """If search_docs raises any Exception, run_docs exits."""
        from navig.commands.docs_cmd import run_docs
        import navig.console_helper as _ch

        with patch("navig.tools.web.search_docs", side_effect=RuntimeError("fail")):
            with patch.object(_ch, "error", create=True, return_value=None):
                with pytest.raises(_EXIT):
                    run_docs(_ctx(), "query", 5, False, False)

    def test_search_import_error_exits(self):
        """If web tools raise ImportError, run_docs exits."""
        from navig.commands.docs_cmd import run_docs
        import navig.console_helper as _ch

        with patch("navig.tools.web.search_docs", side_effect=ImportError("no module")):
            with patch.object(_ch, "error", create=True, return_value=None):
                with pytest.raises(_EXIT):
                    run_docs(_ctx(), "query", 5, False, False)


# ===========================================================================
# run_fetch
# ===========================================================================

class TestRunFetch:
    def _fake_result(self, success: bool = True):
        r = MagicMock()
        r.success = success
        r.final_url = "https://example.com"
        r.title = "Example"
        r.text = "Some content here"
        r.truncated = False
        r.error = None if success else "timeout"
        return r

    @pytest.mark.skipif(not _has_tools_web(), reason="navig.tools.web not available")
    def test_fetch_success_plain(self):
        from navig.commands.docs_cmd import run_fetch

        with patch("navig.tools.web.web_fetch", return_value=self._fake_result()):
            run_fetch(_ctx(), "https://example.com", "markdown", 10000, 30, True, False)

    @pytest.mark.skipif(not _has_tools_web(), reason="navig.tools.web not available")
    def test_fetch_success_rich(self):
        from navig.commands.docs_cmd import run_fetch

        with patch("navig.tools.web.web_fetch", return_value=self._fake_result()):
            run_fetch(_ctx(), "https://example.com", "markdown", 10000, 30, False, False)

    @pytest.mark.skipif(not _has_tools_web(), reason="navig.tools.web not available")
    def test_fetch_json_output(self):
        from navig.commands.docs_cmd import run_fetch

        with patch("navig.tools.web.web_fetch", return_value=self._fake_result()):
            run_fetch(_ctx(), "https://example.com", "markdown", 10000, 30, False, True)

    @pytest.mark.skipif(not _has_tools_web(), reason="navig.tools.web not available")
    def test_fetch_failure_exits(self):
        from navig.commands.docs_cmd import run_fetch
        import navig.console_helper as _ch

        with patch("navig.tools.web.web_fetch", return_value=self._fake_result(success=False)):
            with patch.object(_ch, "error", create=True, return_value=None):
                with pytest.raises(_EXIT):
                    run_fetch(_ctx(), "https://bad.url", "markdown", 10000, 30, False, False)

    def test_fetch_import_error_exits(self):
        from navig.commands.docs_cmd import run_fetch
        import navig.console_helper as _ch

        with patch("navig.tools.web.web_fetch", side_effect=ImportError("missing")):
            with patch.object(_ch, "error", create=True, return_value=None):
                with pytest.raises(_EXIT):
                    run_fetch(_ctx(), "https://example.com", "markdown", 10000, 30, False, False)

    def test_fetch_runtime_error_exits(self):
        from navig.commands.docs_cmd import run_fetch
        import navig.console_helper as _ch

        with patch("navig.tools.web.web_fetch", side_effect=ValueError("bad url")):
            with patch.object(_ch, "error", create=True, return_value=None):
                with pytest.raises(_EXIT):
                    run_fetch(_ctx(), "https://example.com", "markdown", 10000, 30, False, False)


# ===========================================================================
# run_search
# ===========================================================================

class TestRunSearch:
    def _fake_search(self, success: bool = True, has_results: bool = True):
        r = MagicMock()
        r.success = success
        r.error = None if success else "api_error"
        if has_results and success:
            hit = MagicMock()
            hit.title = "Example"
            hit.url = "https://example.com"
            hit.snippet = "An example result"
            r.results = [hit]
        else:
            r.results = []
        return r

    @pytest.mark.skipif(not _has_tools_web(), reason="navig.tools.web not available")
    def test_search_success_rich(self):
        from navig.commands.docs_cmd import run_search

        with patch("navig.tools.web.web_search", return_value=self._fake_search()):
            run_search(_ctx(), "example", 5, "auto", False, False)

    @pytest.mark.skipif(not _has_tools_web(), reason="navig.tools.web not available")
    def test_search_success_plain(self):
        from navig.commands.docs_cmd import run_search

        with patch("navig.tools.web.web_search", return_value=self._fake_search()):
            run_search(_ctx(), "example", 5, "auto", True, False)

    @pytest.mark.skipif(not _has_tools_web(), reason="navig.tools.web not available")
    def test_search_json_output(self):
        from navig.commands.docs_cmd import run_search

        with patch("navig.tools.web.web_search", return_value=self._fake_search()):
            run_search(_ctx(), "example", 5, "auto", False, True)

    @pytest.mark.skipif(not _has_tools_web(), reason="navig.tools.web not available")
    def test_search_no_results(self):
        from navig.commands.docs_cmd import run_search

        with patch("navig.tools.web.web_search", return_value=self._fake_search(has_results=False)):
            run_search(_ctx(), "nothing", 5, "auto", False, False)

    @pytest.mark.skipif(not _has_tools_web(), reason="navig.tools.web not available")
    def test_search_failure_exits(self):
        from navig.commands.docs_cmd import run_search
        import navig.console_helper as _ch

        with patch("navig.tools.web.web_search", return_value=self._fake_search(success=False, has_results=False)):
            with patch.object(_ch, "error", create=True, return_value=None):
                with pytest.raises(_EXIT):
                    run_search(_ctx(), "query", 5, "auto", False, False)

    def test_search_import_error_exits(self):
        from navig.commands.docs_cmd import run_search
        import navig.console_helper as _ch

        with patch("navig.tools.web.web_search", side_effect=ImportError("no module")):
            with patch.object(_ch, "error", create=True, return_value=None):
                with pytest.raises(_EXIT):
                    run_search(_ctx(), "q", 5, "auto", False, False)

    def test_search_runtime_error_exits(self):
        from navig.commands.docs_cmd import run_search
        import navig.console_helper as _ch

        with patch("navig.tools.web.web_search", side_effect=ValueError("bad")):
            with patch.object(_ch, "error", create=True, return_value=None):
                with pytest.raises(_EXIT):
                    run_search(_ctx(), "q", 5, "auto", False, False)


# ===========================================================================
# import_cmd — _persist_bookmarks
# ===========================================================================

class TestPersistBookmarks:
    def _make_items(self):
        return [
            {"type": "bookmark", "value": "https://example.com", "label": "Ex", "source": "chrome", "meta": {"folder": "Work"}},
            {"type": "bookmark", "value": "https://other.com", "label": "Other", "source": "firefox", "meta": {}},
            {"type": "note", "value": "some note"},            # skipped
            {"type": "bookmark", "value": "", "label": "Empty"},  # skipped: no URL
        ]

    def test_persist_adds_new_bookmarks(self):
        from navig.commands.import_cmd import _persist_bookmarks

        mock_db = MagicMock()
        mock_db.get_by_url.return_value = None

        with patch("navig.commands.import_cmd.links_db_mod") as mock_mod:
            mock_mod.get_links_db.return_value = mock_db
            with patch("navig.commands.import_cmd._flatten", return_value=self._make_items()):
                added, skipped = _persist_bookmarks({})

        assert added == 2
        assert skipped == 0

    def test_persist_skips_duplicate(self):
        from navig.commands.import_cmd import _persist_bookmarks

        mock_db = MagicMock()
        mock_db.get_by_url.return_value = {"url": "exists"}

        with patch("navig.commands.import_cmd.links_db_mod") as mock_mod:
            mock_mod.get_links_db.return_value = mock_db
            with patch("navig.commands.import_cmd._flatten", return_value=self._make_items()):
                added, skipped = _persist_bookmarks({})

        assert added == 0
        assert skipped == 2

    def test_persist_sets_folder_notes(self):
        from navig.commands.import_cmd import _persist_bookmarks

        mock_db = MagicMock()
        mock_db.get_by_url.return_value = None

        items = [{"type": "bookmark", "value": "https://a.com", "label": "A", "source": "chrome", "meta": {"folder": "MyFolder"}}]

        with patch("navig.commands.import_cmd.links_db_mod") as mock_mod:
            mock_mod.get_links_db.return_value = mock_db
            with patch("navig.commands.import_cmd._flatten", return_value=items):
                added, _ = _persist_bookmarks({})

        assert added == 1
        call_args = mock_db.add.call_args
        all_args_str = str(call_args)
        assert "MyFolder" in all_args_str

    def test_persist_no_folder_notes_none(self):
        from navig.commands.import_cmd import _persist_bookmarks

        mock_db = MagicMock()
        mock_db.get_by_url.return_value = None

        items = [{"type": "bookmark", "value": "https://b.com", "label": "B", "source": "ff", "meta": {}}]

        with patch("navig.commands.import_cmd.links_db_mod") as mock_mod:
            mock_mod.get_links_db.return_value = mock_db
            with patch("navig.commands.import_cmd._flatten", return_value=items):
                added, _ = _persist_bookmarks({})

        assert added == 1

    def test_persist_returns_tuple(self):
        from navig.commands.import_cmd import _persist_bookmarks

        mock_db = MagicMock()
        mock_db.get_by_url.return_value = None

        with patch("navig.commands.import_cmd.links_db_mod") as mock_mod:
            mock_mod.get_links_db.return_value = mock_db
            with patch("navig.commands.import_cmd._flatten", return_value=[]):
                result = _persist_bookmarks({})

        assert result == (0, 0)


# ===========================================================================
# import_cmd — list_sources
# ===========================================================================

class TestListSources:
    def test_list_sources_prints_all(self, capsys):
        from navig.commands.import_cmd import list_sources

        with patch("navig.commands.import_cmd.UniversalImporter") as MockImporter:
            MockImporter.return_value.list_sources.return_value = ["chrome", "firefox", "safari"]
            list_sources()

        captured = capsys.readouterr()
        assert "chrome" in captured.out
        assert "firefox" in captured.out

    def test_list_sources_empty(self, capsys):
        from navig.commands.import_cmd import list_sources

        with patch("navig.commands.import_cmd.UniversalImporter") as MockImporter:
            MockImporter.return_value.list_sources.return_value = []
            list_sources()

        captured = capsys.readouterr()
        assert captured.out == ""


# ===========================================================================
# import_cmd — _run_import validation
# ===========================================================================

class TestRunImport:
    def _engine(self, sources=None):
        e = MagicMock()
        e.list_sources.return_value = list(sources or ["chrome", "firefox"])
        e.run_one.return_value = []
        e.run_all.return_value = {}
        e.export_json.return_value = "[]"
        return e

    def test_unknown_source_exits(self):
        from navig.commands.import_cmd import _run_import
        import navig.console_helper as _ch

        with patch("navig.commands.import_cmd.UniversalImporter", return_value=self._engine()):
            with patch.object(_ch, "error", create=True, return_value=None):
                with pytest.raises(_EXIT):
                    _run_import(source="nonexistent", path=None, output=None, persist_bookmarks=False, json_output=False)

    def test_path_with_all_exits(self, tmp_path):
        from navig.commands.import_cmd import _run_import
        import navig.console_helper as _ch

        existing = tmp_path / "file.json"
        existing.write_text("{}", encoding="utf-8")

        with patch("navig.commands.import_cmd.UniversalImporter", return_value=self._engine()):
            with patch.object(_ch, "error", create=True, return_value=None):
                with pytest.raises(_EXIT):
                    _run_import(source="all", path=str(existing), output=None, persist_bookmarks=False, json_output=False)

    def test_path_not_exists_exits(self, tmp_path):
        from navig.commands.import_cmd import _run_import
        import navig.console_helper as _ch

        with patch("navig.commands.import_cmd.UniversalImporter", return_value=self._engine()):
            with patch.object(_ch, "error", create=True, return_value=None):
                with pytest.raises(_EXIT):
                    _run_import(source="chrome", path=str(tmp_path / "no_such.json"), output=None, persist_bookmarks=False, json_output=False)

    def test_run_all_no_persist(self):
        from navig.commands.import_cmd import _run_import
        import navig.console_helper as _ch

        engine = self._engine()

        with patch("navig.commands.import_cmd.UniversalImporter", return_value=engine):
            with patch("navig.commands.import_cmd._flatten", return_value=[]):
                with patch.object(_ch, "warning", create=True, return_value=None):
                    _run_import(source="all", path=None, output=None, persist_bookmarks=False, json_output=False)

        engine.run_all.assert_called_once()

    def test_run_one_source(self):
        from navig.commands.import_cmd import _run_import
        import navig.console_helper as _ch

        engine = self._engine()

        with patch("navig.commands.import_cmd.UniversalImporter", return_value=engine):
            with patch("navig.commands.import_cmd._flatten", return_value=[]):
                with patch.object(_ch, "warning", create=True, return_value=None):
                    _run_import(source="chrome", path=None, output=None, persist_bookmarks=False, json_output=False)

        engine.run_one.assert_called_once_with("chrome", path=None)

    def test_json_output_prints(self, capsys):
        from navig.commands.import_cmd import _run_import

        engine = self._engine()
        engine.export_json.return_value = '[{"source":"chrome"}]'

        with patch("navig.commands.import_cmd.UniversalImporter", return_value=engine):
            with patch("navig.commands.import_cmd._flatten", return_value=[]):
                _run_import(source="all", path=None, output=None, persist_bookmarks=False, json_output=True)

        captured = capsys.readouterr()
        assert "chrome" in captured.out

    def test_output_writes_file(self, tmp_path):
        from navig.commands.import_cmd import _run_import
        import navig.console_helper as _ch

        engine = self._engine()
        out_file = tmp_path / "result.json"

        with patch("navig.commands.import_cmd.UniversalImporter", return_value=engine):
            with patch("navig.commands.import_cmd._flatten", return_value=[]):
                with patch("navig.core.yaml_io.atomic_write_text") as mock_write:
                    with patch.object(_ch, "success", create=True, return_value=None):
                        with patch.object(_ch, "warning", create=True, return_value=None):
                            _run_import(source="all", path=None, output=str(out_file), persist_bookmarks=False, json_output=False)

        mock_write.assert_called_once()

    def test_value_error_exits(self):
        from navig.commands.import_cmd import _run_import
        import navig.console_helper as _ch

        engine = self._engine()
        engine.run_all.side_effect = ValueError("bad config")

        with patch("navig.commands.import_cmd.UniversalImporter", return_value=engine):
            with patch.object(_ch, "error", create=True, return_value=None):
                with pytest.raises(_EXIT):
                    _run_import(source="all", path=None, output=None, persist_bookmarks=False, json_output=False)
