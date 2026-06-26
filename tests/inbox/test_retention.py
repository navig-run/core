"""Tests for never-delete retention (navig.inbox.retention)."""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.inbox import retention


def test_preserve_original_keeps_source(tmp_path: Path) -> None:
    src = tmp_path / "photo.png"
    src.write_bytes(b"\x89PNG-bytes")
    rel = retention.preserve_original(src, tmp_path)
    assert rel.startswith(".navig/wiki/_originals/")
    assert (tmp_path / rel).exists()
    assert src.exists()  # original NEVER removed


def test_preserve_original_idempotent(tmp_path: Path) -> None:
    src = tmp_path / "a.bin"
    src.write_bytes(b"same-bytes")
    r1 = retention.preserve_original(src, tmp_path)
    r2 = retention.preserve_original(src, tmp_path)
    assert r1 == r2  # content-addressed → same destination


def test_archive_moves_never_deletes(tmp_path: Path) -> None:
    src = tmp_path / "old.md"
    src.write_text("stale", encoding="utf-8")
    dest = retention.archive(src, tmp_path, reason="superseded")
    assert dest is not None and dest.exists()
    assert not src.exists()  # moved, not destroyed
    assert dest.read_text(encoding="utf-8") == "stale"
    assert dest.with_suffix(dest.suffix + ".archived").exists()


def test_retention_never_calls_os_remove(tmp_path: Path, monkeypatch) -> None:
    import os

    def _boom(*a, **k):  # pragma: no cover — must never be reached
        raise AssertionError("os.remove must never be called on a user file")

    monkeypatch.setattr(os, "remove", _boom)
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: _boom())

    src = tmp_path / "keep.png"
    src.write_bytes(b"data")
    retention.preserve_original(src, tmp_path)
    src2 = tmp_path / "move.md"
    src2.write_text("x", encoding="utf-8")
    retention.archive(src2, tmp_path, reason="r")
