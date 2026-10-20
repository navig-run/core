"""Exact text I/O for files the operator owns.

An in-place edit of a user's markdown must change the bytes it means to change
and nothing else. Two things break that silently:

* **Line endings.** ``Path.read_text`` folds ``\\r\\n`` to ``\\n`` and a text-mode
  write expands ``\\n`` to the platform separator — so on Windows, toggling one
  checkbox in an LF file rewrote the WHOLE file as CRLF, and git showed every
  line changed. Reads here keep endings (``newline=""``) and writes emit them
  verbatim.
* **Encoding.** A file that is not UTF-8 decoded with ``errors="replace"`` and
  written back is corrupted for good. Mutations decode strictly and refuse
  rather than guess.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

__all__ = [
    "NotUtf8Error",
    "dominant_eol",
    "fingerprint",
    "normalize_eol",
    "read_exact",
    "write_exact",
]


class NotUtf8Error(ValueError):
    """The file is not valid UTF-8, so it cannot be rewritten without corrupting it."""


def read_exact(path: Path) -> str:
    """The file's text with its line endings intact. Raises :class:`NotUtf8Error`."""
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NotUtf8Error(f"{path.name} is not UTF-8 — refusing to rewrite it") from exc
    return text[1:] if text.startswith("﻿") else text


def write_exact(path: Path, content: str) -> None:
    """Atomically write *content* byte-for-byte (no newline translation)."""
    from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

    atomic_write_text(path, content, newline="")


def fingerprint(text: str) -> str:
    """Short, stable content hash used as an optimistic-concurrency token."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def dominant_eol(text: str) -> str:
    """``"\\r\\n"`` when most line breaks are CRLF, else ``"\\n"``."""
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def normalize_eol(text: str, eol: str) -> str:
    """Rewrite every line break in *text* as *eol*."""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    return unified if eol == "\n" else unified.replace("\n", eol)
