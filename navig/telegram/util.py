"""Shared helpers: filename/title normalization for duplicate matching.

Ported from the tg-music-bot engine (pure stdlib). Aggressively normalizes
titles/artists/filenames so re-uploads and re-encodes collide.
"""

from __future__ import annotations

import re
import unicodedata

_URL = re.compile(r"(https?://\S+|www\.\S+|t\.me/\S+|kissvk\.com|vk\.com\S*)", re.I)
_EXT = re.compile(r"\.(mp3|flac|m4a|aac|ogg|opus|wav|wma|mp4|mkv|webm|mov)\b", re.I)
_BRACKETED = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")
_JUNK = re.compile(
    r"\b(official\s*(music\s*)?video|official\s*audio|lyric[s]?\s*video|clip\s*officiel|"
    r"visualizer|audio\s*only|video\s*officiel|full\s*album|free\s*download|prod\b.*|"
    r"remaster(ed)?|explicit|hd|hq|new|клип|текст|текстом)\b",
    re.I,
)
_TRACKNO = re.compile(r"^\s*\d{1,3}\s*[\.\-\)]\s*")
_NONWORD = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def norm(s: str | None) -> str:
    """Aggressively normalize a title/artist/filename for fuzzy duplicate matching."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = _URL.sub(" ", s)
    s = _EXT.sub(" ", s)
    s = _BRACKETED.sub(" ", s)
    s = _JUNK.sub(" ", s)
    s = _TRACKNO.sub("", s)
    s = _NONWORD.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


def artist_title_key(performer: str | None, title: str | None, file_name: str | None) -> str:
    """Best-effort normalized 'artist title' key from the richest fields available."""
    if performer or title:
        return norm(f"{performer or ''} {title or ''}").strip()
    return norm(file_name)


def dur_bucket(seconds, width: int = 2) -> int:
    """Round duration to a small bucket so 239s/240s/241s collide."""
    try:
        return int(round(float(seconds) / width)) * width
    except (TypeError, ValueError):
        return -1


# ── Link extraction (tiktok / youtube / generic url) ─────────────────────────

_LINK_RE = re.compile(r"(https?://[^\s<>\"']+|t\.me/[^\s<>\"']+|www\.[^\s<>\"']+)", re.I)
_TIKTOK = re.compile(r"(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)", re.I)
_YOUTUBE = re.compile(r"(youtube\.com|youtu\.be)", re.I)


def classify_link(url: str) -> str:
    """Return a coarse provider tag for a URL: tiktok | youtube | telegram | url."""
    if _TIKTOK.search(url):
        return "tiktok"
    if _YOUTUBE.search(url):
        return "youtube"
    if url.lower().startswith("t.me") or "t.me/" in url.lower():
        return "telegram"
    return "url"


def extract_links(text: str | None) -> list[dict]:
    """Pull URLs from message text → ``[{url, provider}]`` (deduped, order-preserving)."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for m in _LINK_RE.findall(text):
        url = m.rstrip(".,)…")
        if url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "provider": classify_link(url)})
    return out
