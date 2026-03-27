"""
NAVIG Media Context Engine — Image Pipeline
============================================

Analyzes an image through a multi-stage pipeline:

  Stage 1 · Classify     Pillow EXIF + mode     → dims / format / GPS / camera
  Stage 2 · Scene        GPT-4o Vision API      → natural-language description
  Stage 3 · OCR          pytesseract            → extracted text (local, free)
  Stage 4 · Reverse      SerpAPI Google Lens    → visual match results
  Stage 5 · Landmark     Google Cloud Vision    → landmark detection (optional)
  Stage 6 · Card         HTML                   → rich Telegram message

Each external call is:
  * Guarded by BudgetGuard  (skip when monthly limit exceeded)
  * Short-circuit via MediaCache  (SHA-256 key, 24 h TTL)
  * Wrapped in asyncio.wait_for(…, timeout=8)
  * Retried up to 2 times on transient errors

Dependencies: Pillow, httpx
Optional:     pytesseract, google-cloud-vision
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import time
from typing import Any, Optional

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


def _env(*keys: str) -> Optional[str]:
    try:
        from navig.vault.resolver import resolve_secret

        return resolve_secret(list(keys))
    except Exception:
        for k in keys:
            v = os.environ.get(k)
            if v:
                return v
        return None


def _json_env(*keys: str) -> Optional[str]:
    try:
        from navig.vault.resolver import resolve_json_str

        return resolve_json_str(list(keys))
    except Exception:
        for k in keys:
            v = os.environ.get(k)
            if v:
                return v
        return None


async def _with_retry(coro_func, retries: int = _RETRIES):
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return await coro_func()
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(1.0 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


# ── Stage implementations ─────────────────────────────────────────────────────


def _stage_classify(file_bytes: bytes) -> dict:
    """Extract image metadata with Pillow (synchronous)."""
    result: dict = {
        "width": None,
        "height": None,
        "format": None,
        "mode": None,
        "exif": {},
        "gps": None,
        "camera": None,
        "taken_at": None,
    }
    try:
        from PIL import Image  # type: ignore
        from PIL.ExifTags import GPSTAGS, TAGS  # type: ignore

        img = Image.open(io.BytesIO(file_bytes))
        result["width"], result["height"] = img.size
        result["format"] = img.format or "?"
        result["mode"] = img.mode

        raw_exif = img._getexif() if hasattr(img, "_getexif") else None
        if raw_exif:
            named: dict = {}
            gps_raw: dict = {}
            for tag_id, value in raw_exif.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag == "GPSInfo":
                    for g_id, g_val in value.items():
                        gps_tag = GPSTAGS.get(g_id, g_id)
                        gps_raw[gps_tag] = g_val
                else:
                    try:
                        named[tag] = str(value)[:200]
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical
            result["exif"] = named
            result["camera"] = named.get("Model") or named.get("Make")
            result["taken_at"] = named.get("DateTimeOriginal") or named.get("DateTime")

            # Convert GPS rational to decimal degrees
            if gps_raw.get("GPSLatitude") and gps_raw.get("GPSLongitude"):

                def _to_deg(vals, ref):
                    d, m, s = [float(v) for v in vals]
                    deg = d + m / 60 + s / 3600
                    if ref in ("S", "W"):
                        deg = -deg
                    return round(deg, 6)

                try:
                    lat = _to_deg(
                        gps_raw["GPSLatitude"], gps_raw.get("GPSLatitudeRef", "N")
                    )
                    lon = _to_deg(
                        gps_raw["GPSLongitude"], gps_raw.get("GPSLongitudeRef", "E")
                    )
                    result["gps"] = {"lat": lat, "lon": lon}
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
    except Exception as exc:
        logger.debug("Pillow classify: %s", exc)
    return result


async def _stage_vision(file_bytes: bytes, budget: BudgetGuard) -> Optional[str]:
    """Describe image using GPT-4o Vision."""
    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        return None
    if not budget.can_afford("openai_vision"):
        logger.debug("ImageEngine: GPT-4o Vision skipped — budget")
        return None
    try:
        import httpx

        from navig.config import get_config_manager

        _vision_model: str = (
            get_config_manager()
            .global_config.get("media_engine", {})
            .get("vision_model", "gpt-4o")
        )
        _vision_max_tokens: int = (
            get_config_manager()
            .global_config.get("media_engine", {})
            .get("vision_max_tokens", 300)
        )
        b64 = base64.b64encode(file_bytes).decode()
        payload = {
            "model": _vision_model,
            "max_tokens": _vision_max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": "low",
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Describe this image concisely (2-4 sentences). "
                                "Focus on what's depicted, key subjects, setting, "
                                "and any notable details. Be factual."
                            ),
                        },
                    ],
                }
            ],
        }

        async def _call():
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(
                    _PRUL.get("openai", {}).get(
                        "chat", "https://api.openai.com/v1/chat/completions"
                    ),
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

        data = await _with_retry(_call)
        description = (
            (data or {})
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        if description:
            budget.charge("openai_vision")
            return description
    except BudgetExceeded:
        pass  # budget limit reached; skip provider
    except Exception as exc:
        logger.debug("GPT-4o Vision error: %s", exc)
    return None


def _stage_ocr(file_bytes: bytes) -> Optional[str]:
    """Extract text from image via pytesseract (synchronous, free)."""
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore

        img = Image.open(io.BytesIO(file_bytes))
        text = pytesseract.image_to_string(img).strip()
        return text if len(text) >= 3 else None
    except Exception as exc:
        logger.debug("OCR: %s", exc)
    return None


async def _stage_serpapi(
    file_bytes: bytes, budget: BudgetGuard
) -> Optional[list[dict]]:
    """Reverse image search via SerpAPI Google Lens."""
    api_key = _env("SERPAPI_KEY", "SERPAPI_API_KEY")
    if not api_key:
        return None
    if not budget.can_afford("serpapi"):
        logger.debug("ImageEngine: SerpAPI skipped — budget")
        return None
    try:
        import httpx

        b64 = base64.b64encode(file_bytes).decode()
        data_url = f"data:image/jpeg;base64,{b64}"

        async def _call():
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.get(
                    _PRUL.get("serpapi", {}).get(
                        "search", "https://serpapi.com/search"
                    ),
                    params={
                        "engine": "google_lens",
                        "url": data_url,
                        "api_key": api_key,
                    },
                )
                r.raise_for_status()
                return r.json()

        data = await _with_retry(_call)
        matches = (data or {}).get("visual_matches", [])[:3]
        if matches:
            budget.charge("serpapi")
            return [
                {
                    "title": m.get("title", ""),
                    "link": m.get("link", ""),
                    "source": m.get("source", ""),
                    "thumbnail": m.get("thumbnail", ""),
                }
                for m in matches
            ]
    except BudgetExceeded:
        pass  # budget limit reached; skip provider
    except Exception as exc:
        logger.debug("SerpAPI Lens error: %s", exc)
    return None


async def _stage_landmark(
    file_bytes: bytes, budget: BudgetGuard
) -> Optional[list[dict]]:
    """Detect landmarks using Google Cloud Vision (optional)."""
    creds_raw = _json_env("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_raw:
        return None
    if not budget.can_afford("google_vision"):
        logger.debug("ImageEngine: Google Vision skipped — budget")
        return None
    try:
        import json

        from google.cloud import vision  # type: ignore
        from google.oauth2 import service_account  # type: ignore

        credentials = None
        try:
            credentials = service_account.Credentials.from_service_account_info(
                json.loads(creds_raw)
            )
        except Exception:
            try:
                credentials = service_account.Credentials.from_service_account_file(
                    creds_raw
                )
            except Exception:
                credentials = None

        client = (
            vision.ImageAnnotatorClient(credentials=credentials)
            if credentials
            else vision.ImageAnnotatorClient()
        )
        image = vision.Image(content=file_bytes)

        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, client.landmark_detection, image),
            timeout=_TIMEOUT,
        )
        landmarks = []
        for lm in response.landmark_annotations:
            loc = None
            if lm.locations:
                ll = lm.locations[0].lat_lng
                loc = {"lat": ll.latitude, "lon": ll.longitude}
            landmarks.append(
                {
                    "name": lm.description,
                    "score": round(lm.score, 3),
                    "location": loc,
                }
            )
        if landmarks:
            budget.charge("google_vision")
            return landmarks
    except asyncio.TimeoutError:
        logger.debug("Google Vision timed out")
    except BudgetExceeded:
        pass  # budget limit reached; skip provider
    except ImportError:
        logger.debug("google-cloud-vision not installed, landmark detection skipped")
    except Exception as exc:
        logger.debug("Google Vision error: %s", exc)
    return None


# ── Card builder ──────────────────────────────────────────────────────────────


def _build_image_card(
    classify: dict,
    description: Optional[str],
    ocr_text: Optional[str],
    serp_matches: Optional[list[dict]],
    landmarks: Optional[list[dict]],
    duration_ms: float,
) -> str:
    """Return an HTML string suitable for Telegram parse_mode=HTML."""
    parts: list[str] = []

    # ---- Header
    if landmarks:
        parts.append(f'🏛 <b>{landmarks[0]["name"]}</b>')
    elif description:
        first_line = description.split(".")[0][:60]
        parts.append(f"🖼 <b>{first_line}</b>")
    else:
        parts.append("🖼 <b>Image</b>")

    # ---- Dimensions / format
    info_bits = []
    if classify.get("width") and classify.get("height"):
        info_bits.append(f'{classify["width"]}×{classify["height"]}')
    if classify.get("format"):
        info_bits.append(classify["format"])
    if classify.get("camera"):
        info_bits.append(f'📷 {classify["camera"]}')
    if info_bits:
        parts.append("📊 " + " · ".join(info_bits))

    # ---- Taken at / GPS
    if classify.get("taken_at"):
        parts.append(f'📅 {classify["taken_at"]}')
    if classify.get("gps"):
        g = classify["gps"]
        maps_url = f'https://maps.google.com/?q={g["lat"]},{g["lon"]}'
        parts.append(f'📍 <a href="{maps_url}">{g["lat"]}, {g["lon"]}</a>')

    # ---- Description
    if description:
        parts.append(f"\n{description}")

    # ---- OCR
    if ocr_text:
        snippet = ocr_text[:200] + ("…" if len(ocr_text) > 200 else "")
        parts.append(f"\n📝 <i>Text detected:</i>\n<code>{snippet}</code>")

    # ---- Landmarks
    if landmarks and len(landmarks) > 0:
        for lm in landmarks[:3]:
            score_pct = int(lm["score"] * 100)
            loc = lm.get("location")
            loc_str = ""
            if loc:
                maps_url = f'https://maps.google.com/?q={loc["lat"]},{loc["lon"]}'
                loc_str = f' — <a href="{maps_url}">map</a>'
            parts.append(f'🏛 {lm["name"]} ({score_pct}%){loc_str}')

    # ---- Reverse search matches
    if serp_matches:
        parts.append("\n🔍 <b>Visual matches:</b>")
        for m in serp_matches[:3]:
            title = (m.get("title") or "")[:50]
            link = m.get("link", "")
            source = m.get("source", "")
            entry = (
                f'• <a href="{link}">{title or source}</a>' if link else f"• {title}"
            )
            if source and link:
                entry += f" <i>({source})</i>"
            parts.append(entry)

    # ---- Footer
    parts.append(f"\n⏱ <i>Analyzed in {duration_ms / 1000:.1f}s</i>")

    return "\n".join(parts)


# ── Public API ────────────────────────────────────────────────────────────────


class ImageEngine:
    """
    Orchestrates image analysis pipeline and returns a ready-to-send
    Telegram HTML card string.

    Usage::

        engine = ImageEngine()
        result = await engine.analyze(file_bytes)
        # result["card"]   — HTML for Telegram
        # result["description"], result["ocr_text"], … — structured data
    """

    def __init__(
        self,
        budget: Optional[BudgetGuard] = None,
        cache: Optional[MediaCache] = None,
    ) -> None:
        self._budget = budget or BudgetGuard()
        self._cache = cache or MediaCache(namespace="image")

    async def analyze(self, file_bytes: bytes) -> dict[str, Any]:
        """
        Run the full image pipeline on *file_bytes*.
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
            loop = asyncio.get_event_loop()

            # Stage 1 — classify (sync, Pillow)
            classify = await loop.run_in_executor(None, _stage_classify, file_bytes)
            result.update(classify)

            # Stage 2, 3, 4 — vision / OCR / reverse search (parallel)
            description: Optional[str] = None
            ocr_text: Optional[str] = None
            serp_matches: Optional[list] = None

            vision_fut = asyncio.wait_for(
                _stage_vision(file_bytes, self._budget), timeout=_TIMEOUT
            )
            ocr_fut = loop.run_in_executor(None, _stage_ocr, file_bytes)
            serp_fut = asyncio.wait_for(
                _stage_serpapi(file_bytes, self._budget), timeout=_TIMEOUT
            )

            try:
                description, ocr_text, serp_matches = await asyncio.gather(
                    vision_fut, ocr_fut, serp_fut, return_exceptions=True
                )
                # gather with return_exceptions returns exception objects — replace with None
                if isinstance(description, Exception):
                    logger.debug("GPT-4o stage exception: %s", description)
                    description = None
                if isinstance(ocr_text, Exception):
                    logger.debug("OCR stage exception: %s", ocr_text)
                    ocr_text = None
                if isinstance(serp_matches, Exception):
                    logger.debug("SerpAPI stage exception: %s", serp_matches)
                    serp_matches = None
            except Exception as exc:
                logger.debug("Parallel stages failed: %s", exc)

            result["description"] = description
            result["ocr_text"] = ocr_text
            result["serp_matches"] = serp_matches

            # Stage 5 — landmark (sequential, needs GPS hint or just raw file)
            landmarks: Optional[list] = None
            try:
                landmarks = await asyncio.wait_for(
                    _stage_landmark(file_bytes, self._budget), timeout=_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.debug("Landmark detection timed out")
            result["landmarks"] = landmarks

            # Stage 6 — card
            elapsed_ms = (time.perf_counter() - t_start) * 1_000
            result["card"] = _build_image_card(
                classify, description, ocr_text, serp_matches, landmarks, elapsed_ms
            )
            result["analyzed_ms"] = round(elapsed_ms)

            self._cache.put(cache_key, result)

        except Exception as exc:
            logger.exception("ImageEngine.analyze failed: %s", exc)
            result.setdefault("card", "🖼 <b>Image</b>\n\n<i>Analysis failed.</i>")

        return result
