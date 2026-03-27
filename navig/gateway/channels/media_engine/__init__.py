"""
NAVIG Media Context Engine
==========================
Extracts rich context from audio and image files received over Telegram.

Submodules:
  budget      — monthly API cost tracking (JSON-based)
  media_cache — SHA-256 file-level cache with TTL
  audio       — Audio context pipeline (mutagen → AudD → Whisper → Spotify/Last.fm)
  image       — Image context pipeline (EXIF → GPT-4o → Tesseract → SerpAPI)
"""

from .audio import AudioEngine
from .image import ImageEngine

__all__ = ["AudioEngine", "ImageEngine"]
