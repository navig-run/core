"""A missing OCR backend looked exactly like a clip with no captions.

Found by driving the real TikTok the operator sent: `analyze_video_file`
returned `ocr_text=None, note=None` — "read it fine, there is no text" — on a
machine where the Tesseract *binary* was installed but the `pytesseract` wrapper
was not. `extract_ocr_text_from_image_bytes` swallows the ImportError and
returns `None`, the same `None` it returns for a blank frame.

`pytesseract` is declared in no manifest at all, so this is the default state of
every install: the on-screen-text half of the Transcript button, the catalog's
image OCR, the inbox extractor and `navig media brief` all report "nothing
found" forever, and nothing anywhere says why.
"""

from __future__ import annotations

import pytest

from navig.core import ocr as ocr_mod


@pytest.fixture(autouse=True)
def _uncached():
    """The probe is cached for the process; each case needs a fresh answer."""
    ocr_mod.ocr_unavailable_reason.cache_clear()
    yield
    ocr_mod.ocr_unavailable_reason.cache_clear()


# ── the probe names which half is missing ─────────────────────────────────────


class TestOcrUnavailableReason:
    def test_a_missing_wrapper_is_named(self, monkeypatch):
        import builtins

        real = builtins.__import__

        def _no_pytesseract(name, *a, **kw):
            if name == "pytesseract":
                raise ImportError("no module named pytesseract")
            return real(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _no_pytesseract)
        assert "pytesseract" in (ocr_mod.ocr_unavailable_reason() or "")

    def test_a_missing_binary_is_named_separately(self, monkeypatch):
        """Wrapper installed, binary absent — the other half of the pair."""
        fake = type(
            "FakePytesseract",
            (),
            {"get_tesseract_version": staticmethod(_raise_missing_binary)},
        )
        monkeypatch.setitem(__import__("sys").modules, "pytesseract", fake)
        reason = ocr_mod.ocr_unavailable_reason() or ""
        assert "Tesseract binary" in reason
        assert "pytesseract package" not in reason, "wrong half blamed"

    def test_a_working_install_reports_no_reason(self, monkeypatch):
        fake = type(
            "FakePytesseract", (), {"get_tesseract_version": staticmethod(lambda: "5.3.0")}
        )
        monkeypatch.setitem(__import__("sys").modules, "pytesseract", fake)
        assert ocr_mod.ocr_unavailable_reason() is None
        assert ocr_mod.ocr_available() is True

    def test_the_probe_is_cached(self, monkeypatch):
        """It spawns `tesseract -v`; once per frame would be unacceptable."""
        calls = {"n": 0}

        def _count():
            calls["n"] += 1
            return "5.3.0"

        fake = type("FakePytesseract", (), {"get_tesseract_version": staticmethod(_count)})
        monkeypatch.setitem(__import__("sys").modules, "pytesseract", fake)
        for _ in range(5):
            ocr_mod.ocr_unavailable_reason()
        assert calls["n"] == 1


def _raise_missing_binary():
    raise OSError("tesseract is not installed")


# ── the analyzer distinguishes "no text" from "no tool" ───────────────────────


class TestAnalyzerNote:
    async def test_no_ocr_backend_yields_a_note(self, monkeypatch, tmp_path):
        from navig.gateway.channels import telegram_catalog_analyzer as an

        monkeypatch.setattr(an.shutil, "which", lambda name: "ffmpeg")

        async def _run(cmd):
            return False

        monkeypatch.setattr(an, "_run_ffmpeg", _run)
        monkeypatch.setattr(an, "ocr_unavailable_reason", lambda: "no pytesseract")

        _tr, ocr, note = await an.analyze_video_file(tmp_path / "c.mp4")

        assert ocr is None
        assert note == "ocr_unavailable"

    async def test_a_genuinely_textless_clip_keeps_a_clean_note(
        self, monkeypatch, tmp_path
    ):
        """OCR installed and it found nothing — that is a real answer, not a gap."""
        from navig.gateway.channels import telegram_catalog_analyzer as an

        monkeypatch.setattr(an.shutil, "which", lambda name: "ffmpeg")

        async def _run(cmd):
            return False

        monkeypatch.setattr(an, "_run_ffmpeg", _run)
        monkeypatch.setattr(an, "ocr_unavailable_reason", lambda: None)

        _tr, ocr, note = await an.analyze_video_file(tmp_path / "c.mp4")

        assert ocr is None
        assert note is None

    async def test_text_found_never_reports_a_gap(self, monkeypatch, tmp_path):
        """A reason that is stale/wrong must not override a real result."""
        from navig.gateway.channels import telegram_catalog_analyzer as an

        monkeypatch.setattr(an.shutil, "which", lambda name: "ffmpeg")

        async def _run(cmd):
            from pathlib import Path

            for part in cmd:
                if str(part).endswith("frame.jpg"):
                    Path(part).write_bytes(b"jpg")
                    return True
            return False

        monkeypatch.setattr(an, "_run_ffmpeg", _run)

        async def _ocr(paths):
            return "BUY NOW"

        monkeypatch.setattr(an, "_ocr_frames", _ocr)
        monkeypatch.setattr(an, "ocr_unavailable_reason", lambda: "no pytesseract")

        _tr, ocr, note = await an.analyze_video_file(tmp_path / "c.mp4")

        assert ocr == "BUY NOW"
        assert note is None
