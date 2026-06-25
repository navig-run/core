"""Tests for the universal inbox extractor (navig.inbox.extract)."""
from __future__ import annotations

from pathlib import Path

from navig.inbox import extract as ex


def test_text_passthrough(tmp_path: Path) -> None:
    p = tmp_path / "note.md"
    p.write_text("# Idea\nBuild the universal inbox.", encoding="utf-8")
    res = ex.extract(p)
    assert res.kind == "text"
    assert res.extracted_by == ["passthrough"]
    assert "universal inbox" in res.text


def test_unknown_binary_never_raises_and_is_preserved(tmp_path: Path) -> None:
    p = tmp_path / "mystery.bin"
    p.write_bytes(b"\x00\x01\x02NAVIG")
    res = ex.extract(p)
    assert res.kind == "binary"
    assert res.errors  # recorded, not raised
    assert len(res.content_hash) == 64  # still hashed → never dropped


def test_image_degrades_gracefully(tmp_path: Path) -> None:
    # Not a real PNG: PIL/OCR fail, but extraction must not raise.
    p = tmp_path / "pic.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")
    res = ex.extract(p)
    assert res.kind == "image"  # classified by suffix, degrades to empty text


def test_image_ocr_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "navig.core.ocr.extract_ocr_text_from_image_bytes",
        lambda b: "INVOICE total $42",
    )
    p = tmp_path / "receipt.png"
    p.write_bytes(b"\x89PNG fake bytes")
    res = ex.extract(p, policy=ex.ExtractPolicy(mode="local"))
    assert res.kind == "image"
    assert "INVOICE total $42" in res.text
    assert "tesseract" in res.extracted_by


def test_audio_transcription_path(monkeypatch, tmp_path: Path) -> None:
    # Stub the STT bridge so no model is needed.
    monkeypatch.setattr(ex, "_transcribe", lambda path, policy, res: ("hello from audio", "whisper_local"))
    p = tmp_path / "voice.mp3"
    p.write_bytes(b"ID3 fake audio bytes")
    res = ex.extract(p, policy=ex.ExtractPolicy(mode="local"))
    assert res.kind == "audio"
    assert res.text == "hello from audio"
    assert "whisper_local" in res.extracted_by


def test_pdf_text_extraction(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ex, "_extract_pdf", lambda p, *, max_pages, errors: ("PDF body text", {"pages": 2}))
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    res = ex.extract(p)
    assert res.kind == "pdf"
    assert res.text == "PDF body text"
    assert res.metadata.get("pages") == 2


def test_cache_hit_on_second_call(tmp_path: Path) -> None:
    class _Cache:
        def __init__(self):
            self.store = {}

        def get(self, k):
            return self.store.get(k)

        def put(self, k, v):
            self.store[k] = v

    cache = _Cache()
    p = tmp_path / "x.bin"
    p.write_bytes(b"some-bytes")
    r1 = ex.extract(p, cache=cache)
    assert r1.cached is False
    r2 = ex.extract(p, cache=cache)
    assert r2.cached is True
    assert r2.kind == r1.kind


def test_cloud_vision_gated_off_in_local_mode(monkeypatch, tmp_path: Path) -> None:
    called = {"vision": False}

    def _fake_vision(raw, budget, res):
        called["vision"] = True
        return "described"

    monkeypatch.setattr(ex, "_maybe_vision", _fake_vision)
    monkeypatch.setattr("navig.core.ocr.extract_ocr_text_from_image_bytes", lambda b: None)
    p = tmp_path / "pic.jpg"
    p.write_bytes(b"\xff\xd8\xff fake jpeg")
    ex.extract(p, policy=ex.ExtractPolicy(mode="local"))
    assert called["vision"] is False  # local mode never calls cloud


def test_to_markdown_shape(tmp_path: Path) -> None:
    res = ex.ExtractResult(text="body", kind="image", extracted_by=["tesseract"], content_hash="abc")
    md = ex.to_markdown(res, source_label="inbox/x.png", original_preserved=".navig/wiki/_originals/ab-x.png")
    assert md.startswith("---")
    assert "kind: image" in md
    assert "original_preserved: .navig/wiki/_originals/ab-x.png" in md
    assert "body" in md
