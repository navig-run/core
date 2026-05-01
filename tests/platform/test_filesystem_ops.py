"""Tests for navig.platform.filesystem_ops."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from navig.platform import filesystem_ops as fs

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp(tmp_path: Path) -> Path:
    return tmp_path


# ── read_file ─────────────────────────────────────────────────────────────────


def test_read_file_basic(tmp: Path) -> None:
    f = tmp / "hello.txt"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    result = fs.read_file(str(f))
    assert "line1" in result
    assert "line2" in result


def test_read_file_with_range(tmp: Path) -> None:
    f = tmp / "range.txt"
    f.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    result = fs.read_file(str(f), offset=2, limit=2)
    assert "b" in result
    assert "c" in result
    assert "Lines 2-3 of 5" in result


def test_read_file_not_found(tmp: Path) -> None:
    result = fs.read_file(str(tmp / "missing.txt"))
    assert result.startswith("Error: File not found")


def test_read_file_directory(tmp: Path) -> None:
    result = fs.read_file(str(tmp))
    assert result.startswith("Error: Path is not a file")


# ── write_file ────────────────────────────────────────────────────────────────


def test_write_file_create(tmp: Path) -> None:
    f = tmp / "out.txt"
    result = fs.write_file(str(f), "hello")
    assert "Written to" in result
    assert f.read_text() == "hello"


def test_write_file_append(tmp: Path) -> None:
    f = tmp / "out.txt"
    f.write_text("first", encoding="utf-8")
    result = fs.write_file(str(f), " second", append=True)
    assert "Appended to" in result
    assert f.read_text() == "first second"


def test_write_file_creates_parents(tmp: Path) -> None:
    f = tmp / "deep" / "dir" / "file.txt"
    fs.write_file(str(f), "content")
    assert f.exists()


# ── copy_path ─────────────────────────────────────────────────────────────────


def test_copy_file(tmp: Path) -> None:
    src = tmp / "src.txt"
    src.write_text("data")
    dst = tmp / "dst.txt"
    result = fs.copy_path(str(src), str(dst))
    assert "Copied file" in result
    assert dst.read_text() == "data"


def test_copy_no_overwrite(tmp: Path) -> None:
    src = tmp / "a.txt"
    dst = tmp / "b.txt"
    src.write_text("x")
    dst.write_text("y")
    result = fs.copy_path(str(src), str(dst))
    assert "Error" in result
    assert "overwrite" in result.lower()


def test_copy_with_overwrite(tmp: Path) -> None:
    src = tmp / "a.txt"
    dst = tmp / "b.txt"
    src.write_text("new")
    dst.write_text("old")
    result = fs.copy_path(str(src), str(dst), overwrite=True)
    assert "Copied" in result
    assert dst.read_text() == "new"


# ── move_path ─────────────────────────────────────────────────────────────────


def test_move_file(tmp: Path) -> None:
    src = tmp / "mv_src.txt"
    dst = tmp / "mv_dst.txt"
    src.write_text("move me")
    result = fs.move_path(str(src), str(dst))
    assert "Moved" in result
    assert not src.exists()
    assert dst.read_text() == "move me"


# ── delete_path ───────────────────────────────────────────────────────────────


def test_delete_file(tmp: Path) -> None:
    f = tmp / "del.txt"
    f.write_text("bye")
    result = fs.delete_path(str(f))
    assert "Deleted file" in result
    assert not f.exists()


def test_delete_empty_dir(tmp: Path) -> None:
    d = tmp / "emptydir"
    d.mkdir()
    result = fs.delete_path(str(d))
    assert "Deleted directory" in result
    assert not d.exists()


def test_delete_non_empty_dir_no_recursive(tmp: Path) -> None:
    d = tmp / "nonempty"
    d.mkdir()
    (d / "file.txt").write_text("x")
    result = fs.delete_path(str(d))
    assert "Error" in result
    assert "recursive" in result.lower()


def test_delete_non_empty_dir_recursive(tmp: Path) -> None:
    d = tmp / "recdir"
    d.mkdir()
    (d / "file.txt").write_text("x")
    result = fs.delete_path(str(d), recursive=True)
    assert "Deleted directory" in result
    assert not d.exists()


# ── list_directory ────────────────────────────────────────────────────────────


def test_list_directory(tmp: Path) -> None:
    (tmp / "alpha.txt").write_text("a")
    (tmp / "beta.txt").write_text("b")
    result = fs.list_directory(str(tmp))
    assert "alpha.txt" in result
    assert "beta.txt" in result


def test_list_directory_pattern(tmp: Path) -> None:
    (tmp / "foo.txt").write_text("a")
    (tmp / "bar.log").write_text("b")
    result = fs.list_directory(str(tmp), pattern="*.txt")
    assert "foo.txt" in result
    assert "bar.log" not in result


def test_list_directory_not_found(tmp: Path) -> None:
    result = fs.list_directory(str(tmp / "missing"))
    assert result.startswith("Error: Directory not found")


# ── search_files ──────────────────────────────────────────────────────────────


def test_search_files(tmp: Path) -> None:
    sub = tmp / "sub"
    sub.mkdir()
    (sub / "match.py").write_text("")
    (sub / "other.txt").write_text("")
    result = fs.search_files(str(tmp), "*.py")
    assert "match.py" in result


def test_search_files_no_match(tmp: Path) -> None:
    result = fs.search_files(str(tmp), "*.nonexistent")
    assert "No matches found" in result


# ── get_file_info ─────────────────────────────────────────────────────────────


def test_get_file_info_file(tmp: Path) -> None:
    f = tmp / "info.txt"
    f.write_text("abc")
    result = fs.get_file_info(str(f))
    assert "File" in result
    assert "Extension" in result


def test_get_file_info_directory(tmp: Path) -> None:
    result = fs.get_file_info(str(tmp))
    assert "Directory" in result
    assert "Contents:" in result


def test_get_file_info_not_found(tmp: Path) -> None:
    result = fs.get_file_info(str(tmp / "ghost.txt"))
    assert result.startswith("Error: Path not found")
