"""NAVIG TikTok — download + rich metadata + AI briefings for TikTok content.

Two layers, both built on the user's own stack:

- **rapidok** (https://github.com/miztizm/rapidok) — the concurrent, organized
  downloader (CLI archival: `navig tiktok download|profile`).
- **yt-dlp** (rapidok's backend) — used directly for rich metadata (description,
  country, stats) + top comments, which feed an AI **briefing** of a video.

Everything is lazily imported, so importing this package never requires rapidok
or yt-dlp to be installed; only the specific call that needs them will raise.
"""
from __future__ import annotations


def rapidok_available() -> bool:
    """True if the `rapidok` package is importable (default-installed with navig)."""
    try:
        import rapidok  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def ytdlp_available() -> bool:
    """True if `yt_dlp` is importable (a dependency of rapidok)."""
    try:
        import yt_dlp  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False
