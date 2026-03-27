"""
NAVIG Media Context Engine — Audio Pipeline
============================================

Analyzes an audio file through a multi-stage pipeline:

  Stage 1 · Metadata     mutagen                → duration / bitrate / format
  Stage 2 · Fingerprint  AudD API               → track / artist / album
  Stage 3 · Transcript   OpenAI Whisper API     → spoken-word content (fallback)
  Stage 4 · Enrichment   Spotify + Last.fm      → genre / play-count / biography
  Stage 5 · Card         task_card.TaskView     → HTML message sent to user

Each external call is:
  * Guarded by BudgetGuard  (skip when monthly limit exceeded)
  * Short-circuit via MediaCache  (SHA-256 key, 24 h TTL)
  * Wrapped in asyncio.wait_for(…, timeout=8)
  * Retried up to 2 times on transient errors

Dependencies: mutagen, httpx, navig.voice.stt (for Whisper)
Optional:     spotipy, pylast
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import time
from typing import Any

from navig.gateway.channels.media_engine.budget import BudgetExceeded, BudgetGuard
from navig.gateway.channels.media_engine.media_cache import MediaCache

try:
    from navig.llm_router import PROVIDER_RESOURCE_URLS as _PRUL
except Exception:  # pragma: no cover
    _PRUL: dict = {}

logger = logging.getLogger(__name__)

_TIMEOUT = 8.0
_RETRIES = 2


# ── Helpers ───────────────────────────────────────────────────────────────────


def _env(*keys: str) -> str | None:
    try:
        from navig.vault.resolver import resolve_secret

        return resolve_secret(list(keys))
    except Exception:
        for k in keys:
            v = os.environ.get(k)
            if v:
                return v
        return None


async def _fetch_json(
    method: str,
    url: str,
    *,
    timeout: float = _TIMEOUT,
    **kwargs: Any,
) -> dict | None:
    """Thin async httpx wrapper — returns None on any failure."""
    try:
        import httpx  # lazy import

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await getattr(client, method)(url, **kwargs)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.debug("AudioEngine HTTP %s %s: %s", method.upper(), url, exc)
        return None


async def _with_retry(coro_func, retries: int = _RETRIES):
    """Retry async callable up to *retries* times on exception."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await coro_func()
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(1.0 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


# ── Stage implementations ─────────────────────────────────────────────────────


def _stage_metadata(file_bytes: bytes) -> dict:
    """Extract metadata via mutagen (synchronous, fast)."""
    result: dict = {
        "format": "audio",
        "duration_sec": None,
        "bitrate": None,
        "title": None,
        "artist": None,
        "album": None,
        "genre": None,
    }
    try:
        import mutagen  # type: ignore

        f = mutagen.File(io.BytesIO(file_bytes))
        if f is None:
            return result
        result["duration_sec"] = (
            int(getattr(f, "info", None) and f.info.length or 0) or None
        )
        result["bitrate"] = int(getattr(getattr(f, "info", None), "bitrate", 0)) or None
        tags = f.tags or {}

        def _tag(*keys):
            for k in keys:
                v = tags.get(k)
                if v:
                    return str(v[0]) if isinstance(v, (list, tuple)) else str(v)
            return None

        result["title"] = _tag("TIT2", "\xa9nam", "title")
        result["artist"] = _tag("TPE1", "\xa9ART", "artist")
        result["album"] = _tag("TALB", "\xa9alb", "album")
        result["genre"] = _tag("TCON", "\xa9gen", "genre")
        result["format"] = type(f).__name__.replace("File", "").upper() or "audio"
    except Exception as exc:
        logger.debug("mutagen metadata: %s", exc)
    return result


async def _stage_audd(file_bytes: bytes, budget: BudgetGuard) -> dict | None:
    """Fingerprint audio using the AudD API."""
    api_key = _env("AUDD_API_KEY")
    if not api_key:
        return None
    if not budget.can_afford("audd"):
        logger.debug("AudioEngine: AudD skipped — budget")
        return None
    try:
        import httpx

        b64 = base64.b64encode(file_bytes).decode()

        async def _call():
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(
                    _PRUL.get("audd", {}).get("base", "https://api.audd.io/"),
                    data={
                        "api_token": api_key,
                        "audio": b64,
                        "return": "spotify,apple_music,deezer",
                    },
                )
                r.raise_for_status()
                return r.json()

        data = await _with_retry(_call)
        if data and data.get("status") == "success" and data.get("result"):
            budget.charge("audd")
            return data["result"]
    except BudgetExceeded:
        pass  # budget limit reached; skip provider
    except Exception as exc:
        logger.debug("AudD error: %s", exc)
    return None


async def _stage_whisper(file_bytes: bytes, budget: BudgetGuard) -> str | None:
    """Transcribe audio using OpenAI Whisper API."""
    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        return None
    if not budget.can_afford("openai_whisper"):
        logger.debug("AudioEngine: Whisper skipped — budget")
        return None
    try:
        import httpx

        async def _call():
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    _PRUL.get("openai", {}).get(
                        "transcriptions",
                        "https://api.openai.com/v1/audio/transcriptions",
                    ),
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": ("audio.mp3", file_bytes, "audio/mpeg")},
                    data={"model": "whisper-1"},
                )
                r.raise_for_status()
                return r.json()

        data = await _with_retry(_call)
        if data and "text" in data:
            budget.charge("openai_whisper")
            return data["text"].strip()
    except BudgetExceeded:
        pass  # budget limit reached; skip provider
    except Exception as exc:
        logger.debug("Whisper error: %s", exc)
    return None


async def _stage_spotify(artist: str, title: str, budget: BudgetGuard) -> dict | None:
    """Enrich with Spotify track info (no charge, public API with client creds)."""
    client_id = _env("SPOTIFY_CLIENT_ID")
    client_secret = _env("SPOTIFY_CLIENT_SECRET")
    if not (client_id and client_secret):
        return None
    try:
        # Obtain bearer token
        auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        token_data = await _fetch_json(
            "post",
            _PRUL.get("spotify", {}).get(
                "token", "https://accounts.spotify.com/api/token"
            ),
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data="grant_type=client_credentials",
        )
        if not token_data or "access_token" not in token_data:
            return None
        token = token_data["access_token"]

        q = f"track:{title} artist:{artist}" if artist else title
        search = await _fetch_json(
            "get",
            _PRUL.get("spotify", {}).get("search", "https://api.spotify.com/v1/search"),
            headers={"Authorization": f"Bearer {token}"},
            params={"q": q, "type": "track", "limit": 1},
        )
        items = (search or {}).get("tracks", {}).get("items", [])
        if not items:
            return None
        item = items[0]
        return {
            "spotify_url": item.get("external_urls", {}).get("spotify"),
            "popularity": item.get("popularity"),
            "preview_url": item.get("preview_url"),
            "album_image": (item.get("album", {}).get("images") or [{}])[0].get("url"),
            "release_date": item.get("album", {}).get("release_date"),
        }
    except Exception as exc:
        logger.debug("Spotify enrichment: %s", exc)
    return None


async def _stage_lastfm(artist: str, title: str, budget: BudgetGuard) -> dict | None:
    """Enrich via Last.fm API (free, no charge)."""
    api_key = _env("LASTFM_API_KEY")
    if not api_key or not artist:
        return None
    try:
        data = await _fetch_json(
            "get",
            _PRUL.get("lastfm", {}).get("base", "https://ws.audioscrobbler.com/2.0/"),
            params={
                "method": "track.getInfo",
                "api_key": api_key,
                "artist": artist,
                "track": title,
                "format": "json",
            },
        )
        track = (data or {}).get("track", {})
        if not track:
            return None
        tags = [t["name"] for t in (track.get("toptags", {}).get("tag") or [])][:3]
        return {
            "playcount": track.get("playcount"),
            "listeners": track.get("listeners"),
            "tags": tags,
            "lastfm_url": track.get("url"),
            "wiki_summary": (track.get("wiki", {}).get("summary") or "")
            .split("<a href")[0]
            .strip()
            or None,
        }
    except Exception as exc:
        logger.debug("Last.fm enrichment: %s", exc)
    return None


# ── Card builder ──────────────────────────────────────────────────────────────


def _build_audio_card(
    metadata: dict,
    audd: dict | None,
    transcript: str | None,
    spotify: dict | None,
    lastfm: dict | None,
    duration_ms: float,
) -> str:
    """Return an HTML string suitable for Telegram parse_mode=HTML."""
    parts: list[str] = []

    # ---- Header
    if audd:
        title = audd.get("title", "?")
        artist = audd.get("artist", "?")
        album = audd.get("album", "")
        parts.append(f"🎵 <b>{title}</b>")
        parts.append(f"👤 {artist}")
        if album:
            parts.append(f"💿 {album}")
    elif metadata.get("title"):
        title = metadata["title"]
        artist = metadata.get("artist", "")
        parts.append(f"🎵 <b>{title}</b>")
        if artist:
            parts.append(f"👤 {artist}")
    else:
        parts.append("🎵 <b>Audio file</b>")

    # ---- Format / duration
    info_bits = []
    if metadata.get("format"):
        info_bits.append(metadata["format"])
    if metadata.get("duration_sec"):
        s = int(metadata["duration_sec"])
        info_bits.append(f"{s // 60}:{s % 60:02d}")
    if metadata.get("bitrate"):
        info_bits.append(f"{metadata['bitrate'] // 1000} kbps")
    if info_bits:
        parts.append("📊 " + " · ".join(info_bits))

    # ---- Enrichment
    if lastfm:
        row = []
        if lastfm.get("listeners"):
            row.append(f"👥 {int(lastfm['listeners']):,} listeners")
        if lastfm.get("tags"):
            row.append("🏷 " + ", ".join(lastfm["tags"]))
        if row:
            parts.append("   ".join(row))
        if lastfm.get("wiki_summary"):
            summary = lastfm["wiki_summary"][:180]
            if len(lastfm["wiki_summary"]) > 180:
                summary += "…"
            parts.append(f"📖 {summary}")

    if spotify and spotify.get("spotify_url"):
        parts.append(f'🎧 <a href="{spotify["spotify_url"]}">Open in Spotify</a>')

    if audd and lastfm and lastfm.get("lastfm_url"):
        parts.append(f'📻 <a href="{lastfm["lastfm_url"]}">Last.fm page</a>')

    # ---- Transcript snippet
    if transcript:
        snippet = transcript[:200] + ("…" if len(transcript) > 200 else "")
        parts.append(f"\n🗣 <i>{snippet}</i>")

    # ---- Footer
    parts.append(f"\n⏱ <i>Analyzed in {duration_ms / 1000:.1f}s</i>")

    return "\n".join(parts)


# ── Public API ────────────────────────────────────────────────────────────────


class AudioEngine:
    """
    Orchestrates audio analysis pipeline and returns a ready-to-send
    Telegram HTML card string.

    Usage::

        engine = AudioEngine()
        result = await engine.analyze(file_bytes)
        # result["card"]  — HTML string for Telegram
        # result["title"], result["artist"], … — structured data
    """

    def __init__(
        self,
        budget: BudgetGuard | None = None,
        cache: MediaCache | None = None,
    ) -> None:
        self._budget = budget or BudgetGuard()
        self._cache = cache or MediaCache(namespace="audio")

    async def analyze(self, file_bytes: bytes) -> dict[str, Any]:
        """
        Run the full audio pipeline on *file_bytes*.
        Returns a dict with at least ``{"card": "<html>", "cached": bool}``.
        Never raises — worst case returns a minimal fallback card.
        """
        t_start = time.perf_counter()
        cache_key = MediaCache.key(file_bytes)

        cached = self._cache.get(cache_key)
        if cached:
            cached["cached"] = True
            return cached

        result: dict[str, Any] = {"cached": False}
        try:
            # Stage 1 — metadata (sync, no API)
            metadata = await asyncio.get_event_loop().run_in_executor(
                None, _stage_metadata, file_bytes
            )
            result.update(metadata)

            # Stage 2 — AudD fingerprint (async, paid)
            audd = None
            try:
                audd = await asyncio.wait_for(
                    _stage_audd(file_bytes, self._budget), timeout=_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.debug("AudD timed out")
            if audd:
                result["audd_title"] = audd.get("title")
                result["audd_artist"] = audd.get("artist")
                result["audd_album"] = audd.get("album")

            # Determine best known title / artist for enrichment
            title = (audd or {}).get("title") or result.get("title") or ""
            artist = (audd or {}).get("artist") or result.get("artist") or ""

            # Stage 3 — Whisper (only if no track ID + file small enough)
            transcript: str | None = None
            if not audd and len(file_bytes) < 25 * 1024 * 1024:
                try:
                    transcript = await asyncio.wait_for(
                        _stage_whisper(file_bytes, self._budget), timeout=60.0
                    )
                except asyncio.TimeoutError:
                    logger.debug("Whisper timed out")
            result["transcript"] = transcript

            # Stage 4 — Spotify + Last.fm (parallel, free/cheap)
            spotify: dict | None = None
            lastfm: dict | None = None
            if title or artist:
                try:
                    spotify, lastfm = await asyncio.wait_for(
                        asyncio.gather(
                            _stage_spotify(artist, title, self._budget),
                            _stage_lastfm(artist, title, self._budget),
                        ),
                        timeout=_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.debug("Enrichment timed out")

            result["spotify"] = spotify
            result["lastfm"] = lastfm

            # Stage 5 — card
            elapsed_ms = (time.perf_counter() - t_start) * 1_000
            result["card"] = _build_audio_card(
                metadata, audd, transcript, spotify, lastfm, elapsed_ms
            )
            result["analyzed_ms"] = round(elapsed_ms)

            self._cache.put(cache_key, result)

        except Exception as exc:
            logger.exception("AudioEngine.analyze failed: %s", exc)
            result.setdefault("card", "🎵 <b>Audio file</b>\n\n<i>Analysis failed.</i>")

        return result
