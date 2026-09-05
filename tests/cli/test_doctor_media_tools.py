"""ffmpeg and OCR are the two dependencies whose absence produces a plausible
empty answer instead of an error — so they are the two that need a doctor row.

Neither had one. On the operator's own machine `pytesseract` was not installed
at all (it is declared in no manifest), so every OCR surface had been returning
"no text found" since the day it shipped, and nothing anywhere said why.
"""

from __future__ import annotations

import pytest

from navig.commands import doctor as doc
from navig.core import ocr as ocr_mod


@pytest.fixture(autouse=True)
def _uncached():
    clear = getattr(ocr_mod.ocr_unavailable_reason, "cache_clear", None)
    if clear:
        clear()
    yield
    clear = getattr(ocr_mod.ocr_unavailable_reason, "cache_clear", None)
    if clear:
        clear()


def _row(rows, needle):
    """Rows are ``(icon, ok, line)`` tuples that also carry label/detail."""
    for r in rows:
        if needle.lower() in r.label.lower():
            return r
    raise AssertionError(f"no row matching {needle!r} in {[r.label for r in rows]}")


class TestMediaToolsSection:
    def test_it_is_collected_into_the_report(self, monkeypatch):
        """A check nothing calls is documentation."""
        monkeypatch.setattr(doc, "check_media_tools", lambda: [doc._check("OCR", True, "x")])
        names = [name for name, _rows in doc._collect_sections(skip_deps=True)]
        assert "Media Tools" in names

    def test_missing_ocr_warns_rather_than_passing(self, monkeypatch):
        """✓ over an unknown is worse than ⚠ — it tells you not to look."""
        monkeypatch.setattr(
            ocr_mod, "ocr_unavailable_reason", lambda: "the pytesseract package isn't installed"
        )
        icon, ok, _line = row = _row(doc.check_media_tools(), "OCR")
        assert ok is False
        assert icon == doc._WARN
        assert "pytesseract" in row.detail
        assert "navig[ocr]" in row.detail, "a warning without a fix is noise"

    def test_working_ocr_passes(self, monkeypatch):
        monkeypatch.setattr(ocr_mod, "ocr_unavailable_reason", lambda: None)
        icon, ok, _line = _row(doc.check_media_tools(), "OCR")
        assert ok is True
        assert icon == doc._OK

    def test_missing_ffmpeg_warns(self, monkeypatch):
        monkeypatch.setattr(doc.shutil, "which", lambda name: None)
        icon, ok, _line = _row(doc.check_media_tools(), "ffmpeg")
        assert ok is False
        assert icon == doc._WARN

    def test_present_ffmpeg_passes(self, monkeypatch):
        monkeypatch.setattr(doc.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        _icon, ok, _line = _row(doc.check_media_tools(), "ffmpeg")
        assert ok is True
